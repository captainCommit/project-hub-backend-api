"""Secrets for integrations: the platform's app credentials, customers' tokens, and signed links.

- Platform credentials (Slack client secret, Microsoft client secret, Teams bot password, ...) come from the
  environment locally, or from SSM SecureStrings under INTEGRATION_SECRETS_PATH, fetched in one call the first
  time any is needed (not at start-up, so cold starts don't pay for them).
- Customers' tokens (Slack bot tokens, Microsoft refresh tokens) are encrypted with a KMS key kept for them, with
  the owning account or person as the encryption context, so a token copied onto another row can't be decrypted.
  Locally a Fernet key derived from INTEGRATION_LOCAL_SECRET stands in; it is refused outside ENVIRONMENT=local.
- Signed tokens carry OAuth state and account-linking requests through the browser, with an expiry.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID

from app.core.config import Settings

SECRET_NAMES = {
    "slack_client_secret": "slack-client-secret",
    "slack_signing_secret": "slack-signing-secret",
    "microsoft_client_secret": "microsoft-client-secret",
    "teams_bot_password": "teams-bot-password",
    "integration_state_secret": "state-secret",
}
LOCAL_STATE_SECRET = "local-dev-only-integration-state-secret"


class IntegrationNotConfigured(Exception):
    """The platform hasn't been set up for this integration (no app credentials)."""


# Secrets read from SSM, per path: (values, when read). Re-read after SECRETS_TTL_SECONDS, so a secret added
# (or rotated) after a container started is picked up without a redeploy.
SECRETS_TTL_SECONDS = 300
_ssm_cache: dict[str, tuple[dict[str, str], float]] = {}


def _ssm_secrets(path: str, region: str) -> dict[str, str]:
    cached = _ssm_cache.get(path)
    if cached is not None and time.time() - cached[1] < SECRETS_TTL_SECONDS:
        return cached[0]
    import boto3

    client = boto3.client("ssm", region_name=region)
    values: dict[str, str] = {}
    for page in client.get_paginator("get_parameters_by_path").paginate(Path=path, WithDecryption=True):
        for parameter in page["Parameters"]:
            values[parameter["Name"].rsplit("/", 1)[-1]] = parameter["Value"]
    _ssm_cache[path] = (values, time.time())
    return values


def forget_cached_secrets() -> None:
    _ssm_cache.clear()


def platform_secret(settings: Settings, field: str) -> str:
    """One of SECRET_NAMES: from the environment, else SSM. Empty if it isn't set up."""
    value = str(getattr(settings, field, "") or "")
    if value:
        return value
    if settings.integration_secrets_path:
        try:
            value = _ssm_secrets(settings.integration_secrets_path.rstrip("/") + "/", settings.aws_region).get(SECRET_NAMES[field], "")
        except Exception:
            import logging

            logging.getLogger(__name__).warning("Integration secrets could not be read from SSM")
            value = ""
    if not value and field == "integration_state_secret" and settings.environment == "local":
        return LOCAL_STATE_SECRET
    return value


def require_secret(settings: Settings, field: str) -> str:
    value = platform_secret(settings, field)
    if not value:
        raise IntegrationNotConfigured(field)
    return value


# Customers' tokens -------------------------------------------------------------------------------------------


class TokenCipher:
    """Encrypts a token for one owner (an account or a person)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.integration_kms_key_id:
            import boto3

            self.kms = boto3.client("kms", region_name=settings.aws_region)
            self.fernet = None
        elif settings.environment == "local" and settings.integration_local_secret:
            from cryptography.fernet import Fernet

            self.kms = None
            self.fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.integration_local_secret.encode()).digest()))
        else:
            raise IntegrationNotConfigured("integration_kms_key_id")

    def encrypt(self, owner: UUID, plaintext: str) -> bytes:
        if self.kms is not None:
            return self.kms.encrypt(
                KeyId=self.settings.integration_kms_key_id, Plaintext=plaintext.encode(), EncryptionContext={"owner": str(owner)}
            )["CiphertextBlob"]
        assert self.fernet is not None
        return self.fernet.encrypt(f"{owner}:{plaintext}".encode())

    def decrypt(self, owner: UUID, ciphertext: bytes) -> str:
        if self.kms is not None:
            return self.kms.decrypt(
                CiphertextBlob=ciphertext, KeyId=self.settings.integration_kms_key_id, EncryptionContext={"owner": str(owner)}
            )["Plaintext"].decode()
        assert self.fernet is not None
        bound, _, plaintext = self.fernet.decrypt(ciphertext).decode().partition(":")
        if bound != str(owner):
            raise ValueError("This token belongs to someone else.")
        return plaintext


# Signed tokens ---------------------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(settings: Settings, purpose: str, payload: dict[str, Any], *, ttl_seconds: int) -> str:
    """A tamper-proof, expiring token for `purpose` carrying `payload`."""
    secret = require_secret(settings, "integration_state_secret").encode()
    body = _b64(json.dumps({"p": purpose, "e": int(time.time()) + ttl_seconds, "d": payload}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def unsign(settings: Settings, purpose: str, token: str) -> dict[str, Any] | None:
    """The payload, or None if the token is forged, for another purpose, or expired."""
    secret = platform_secret(settings, "integration_state_secret").encode()
    if not secret or "." not in token:
        return None
    body, _, signature = token.partition(".")
    expected = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("p") != purpose or int(data.get("e", 0)) < time.time():
        return None
    return data.get("d") or {}
