"""Microsoft Teams through the Bot Framework: checking incoming activities, and sending messages.

One bot (an Azure Bot resource with its own Entra app) serves every account. Teams sends it an activity when it
is added to a team or a personal chat and whenever someone messages it; each carries a JWT signed by the Bot
Framework, checked here before anything is read. Sending uses a token for the bot's own app.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.services.integrations.http import Http
from app.services.integrations.secrets import require_secret

OPENID_CONFIG_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"
TOKEN_SCOPE = "https://api.botframework.com/.default"
KEYS_TTL_SECONDS = 24 * 3600
CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
# Service URLs Teams uses; anything else is refused so a forged activity can't make the bot call out elsewhere.
ALLOWED_SERVICE_URL_PREFIXES = ("https://smba.trafficmanager.net/", "https://smba.infra.gcc.teams.microsoft.com/", "https://smba.infra.dod.teams.microsoft.us/")

_lock = threading.Lock()
_keys: dict[str, Any] = {"value": None, "expires": 0.0}
_token: dict[str, Any] = {"value": None, "expires": 0.0}


class TeamsError(Exception):
    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_gone(self) -> bool:
        """The conversation no longer exists or the bot was removed from it."""
        return self.status in (403, 404)


def service_url_allowed(url: str) -> bool:
    return url.startswith(ALLOWED_SERVICE_URL_PREFIXES)


def _signing_keys(http: Http) -> dict[str, Any]:
    with _lock:
        if _keys["value"] is not None and _keys["expires"] > time.time():
            return _keys["value"]
    config = http("GET", OPENID_CONFIG_URL).data
    keys = http("GET", config["jwks_uri"]).data
    with _lock:
        _keys.update(value=keys, expires=time.time() + KEYS_TTL_SECONDS)
    return keys


def verify_activity(settings: Settings, http: Http, authorization: str | None, activity: dict[str, Any]) -> bool:
    """Whether the activity really comes from the Bot Framework, for this bot, about its stated service URL."""
    from jose import JWTError, jwt

    if not settings.teams_bot_app_id or not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization[len("Bearer ") :]
    try:
        claims = jwt.decode(
            token,
            _signing_keys(http),
            algorithms=["RS256"],
            audience=settings.teams_bot_app_id,
            issuer=ISSUER,
            options={"leeway": 300},
        )
    except (JWTError, KeyError, TypeError, ValueError):
        return False
    service_url = str(activity.get("serviceUrl") or "")
    return claims.get("serviceurl", service_url) == service_url and service_url_allowed(service_url)


def bot_token(settings: Settings, http: Http) -> str:
    with _lock:
        if _token["value"] and _token["expires"] > time.time() + 60:
            return str(_token["value"])
    response = http(
        "POST",
        f"https://login.microsoftonline.com/{settings.teams_bot_tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.teams_bot_app_id,
            "client_secret": require_secret(settings, "teams_bot_password"),
            "scope": TOKEN_SCOPE,
        },
    )
    if not response.ok or not isinstance(response.data, dict):
        raise TeamsError("The Teams bot couldn't sign in.", status=response.status)
    with _lock:
        _token.update(value=response.data["access_token"], expires=time.time() + int(response.data.get("expires_in", 3600)))
    return str(response.data["access_token"])


def forget_cached_credentials() -> None:
    with _lock:
        _keys.update(value=None, expires=0.0)
        _token.update(value=None, expires=0.0)


@dataclass
class TeamsClient:
    settings: Settings
    http: Http

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.http(
            "POST", url, headers={"Authorization": f"Bearer {bot_token(self.settings, self.http)}"}, json=body
        )
        if not response.ok:
            detail = response.data.get("error", {}).get("message") if isinstance(response.data, dict) else None
            raise TeamsError(str(detail or f"Teams returned {response.status}."), status=response.status)
        return response.data if isinstance(response.data, dict) else {}

    def send_card(self, service_url: str, conversation_id: str, card: dict[str, Any], *, summary: str) -> None:
        if not service_url_allowed(service_url):
            raise TeamsError("Unexpected Teams service URL.", status=400)
        self._post(
            f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities",
            {"type": "message", "summary": summary[:200], "attachments": [{"contentType": CARD_CONTENT_TYPE, "content": card}]},
        )

    def send_text(self, service_url: str, conversation_id: str, text: str) -> None:
        if not service_url_allowed(service_url):
            raise TeamsError("Unexpected Teams service URL.", status=400)
        self._post(
            f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities",
            {"type": "message", "textFormat": "markdown", "text": text},
        )
