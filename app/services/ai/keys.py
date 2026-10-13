"""Encrypting accounts' own model API keys.

Deployed, keys are encrypted with a KMS key kept only for this. The account id is the KMS encryption context,
so a key copied onto another account's row can't be decrypted. The API's role may only encrypt (when an owner
saves a key); only the AI worker's role may decrypt.

Locally there is no KMS, so a Fernet key derived from AI_LOCAL_KEY_SECRET stands in, with the account id
bound into the ciphertext the same way. It is refused outside ENVIRONMENT=local.
"""

import base64
import hashlib
from typing import Protocol
from uuid import UUID

from app.core.config import Settings


class KeyStorageUnavailable(Exception):
    """No KMS key is configured, so account keys can't be stored."""


class KeyCipher(Protocol):
    def encrypt(self, account_id: UUID, plaintext: str) -> bytes: ...

    def decrypt(self, account_id: UUID, ciphertext: bytes) -> str: ...


class KmsKeyCipher:
    def __init__(self, key_id: str, region: str) -> None:
        import boto3

        self.key_id = key_id
        self.client = boto3.client("kms", region_name=region)

    def encrypt(self, account_id: UUID, plaintext: str) -> bytes:
        response = self.client.encrypt(
            KeyId=self.key_id,
            Plaintext=plaintext.encode(),
            EncryptionContext={"account_id": str(account_id)},
        )
        return response["CiphertextBlob"]

    def decrypt(self, account_id: UUID, ciphertext: bytes) -> str:
        response = self.client.decrypt(
            CiphertextBlob=ciphertext,
            KeyId=self.key_id,
            EncryptionContext={"account_id": str(account_id)},
        )
        return response["Plaintext"].decode()


class LocalKeyCipher:
    def __init__(self, secret: str) -> None:
        from cryptography.fernet import Fernet

        self.fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

    def encrypt(self, account_id: UUID, plaintext: str) -> bytes:
        return self.fernet.encrypt(f"{account_id}:{plaintext}".encode())

    def decrypt(self, account_id: UUID, ciphertext: bytes) -> str:
        bound, _, plaintext = self.fernet.decrypt(ciphertext).decode().partition(":")
        if bound != str(account_id):
            raise ValueError("This key belongs to a different account.")
        return plaintext


def key_cipher(settings: Settings) -> KeyCipher:
    if settings.ai_kms_key_id:
        return KmsKeyCipher(settings.ai_kms_key_id, settings.aws_region)
    if settings.environment == "local" and settings.ai_local_key_secret:
        return LocalKeyCipher(settings.ai_local_key_secret)
    raise KeyStorageUnavailable


def last4(api_key: str) -> str:
    return api_key[-4:] if len(api_key) >= 8 else ""
