"""Microsoft Graph for Outlook: signing in with a work account, the calendar, and sending mail.

One multi-tenant Entra app serves every customer. A person connecting their calendar consents to
Calendars.ReadWrite; an account owner connecting a mailbox for notification email consents to Mail.Send while
signed in as that mailbox. Either way only a refresh token is kept (encrypted). Microsoft rotates it on every
use, so the newest one is saved each time.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.core.config import Settings
from app.services.integrations.http import Http
from app.services.integrations.secrets import require_secret

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = {
    "calendar": "offline_access openid email User.Read Calendars.ReadWrite",
    "mail": "offline_access openid email User.Read Mail.Send",
}
# Refresh-token errors that mean the person has to connect again.
REAUTH_ERRORS = {"invalid_grant", "interaction_required", "consent_required", "unauthorized_client"}


class GraphError(Exception):
    def __init__(self, message: str, *, status: int = 0, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def needs_reconnect(self) -> bool:
        return self.status == 401 or (self.code or "") in REAUTH_ERRORS


def redirect_uri(settings: Settings) -> str:
    return f"{settings.app_url.rstrip('/')}/api/v1/integrations/microsoft/callback"


def authorize_url(settings: Settings, purpose: str, state: str) -> str:
    return f"{AUTHORITY}/authorize?" + urlencode(
        {
            "client_id": settings.microsoft_client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri(settings),
            "response_mode": "query",
            "scope": SCOPES[purpose],
            "state": state,
            "prompt": "select_account",
        }
    )


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str


def _token_request(settings: Settings, http: Http, purpose: str, grant: dict[str, str]) -> Tokens:
    response = http(
        "POST",
        f"{AUTHORITY}/token",
        data={
            "client_id": settings.microsoft_client_id,
            "client_secret": require_secret(settings, "microsoft_client_secret"),
            "scope": SCOPES[purpose],
            "redirect_uri": redirect_uri(settings),
            **grant,
        },
    )
    data = response.data if isinstance(response.data, dict) else {}
    if not response.ok or "access_token" not in data:
        raise GraphError(str(data.get("error_description") or "Microsoft sign-in failed."), status=response.status, code=data.get("error"))
    return Tokens(access_token=data["access_token"], refresh_token=data.get("refresh_token") or grant.get("refresh_token", ""))


def exchange_code(settings: Settings, http: Http, purpose: str, code: str) -> Tokens:
    return _token_request(settings, http, purpose, {"grant_type": "authorization_code", "code": code})


def refresh(settings: Settings, http: Http, purpose: str, refresh_token: str) -> Tokens:
    return _token_request(settings, http, purpose, {"grant_type": "refresh_token", "refresh_token": refresh_token})


class GraphClient:
    def __init__(self, access_token: str, http: Http) -> None:
        self.access_token = access_token
        self.http = http

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        response = self.http(
            method,
            GRAPH + path,
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
            json=body,
            params=params,
        )
        if response.status == 404 and method in ("PATCH", "DELETE"):
            raise GraphError("Not found.", status=404)
        if not response.ok:
            error = response.data.get("error", {}) if isinstance(response.data, dict) else {}
            raise GraphError(str(error.get("message") or f"Microsoft Graph returned {response.status}."), status=response.status, code=error.get("code"))
        return response.data

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/me", params={"$select": "id,displayName,mail,userPrincipalName"})

    # Calendar

    def create_calendar(self, name: str) -> str:
        return str(self.request("POST", "/me/calendars", {"name": name})["id"])

    def calendar_exists(self, calendar_id: str) -> bool:
        try:
            self.request("GET", f"/me/calendars/{calendar_id}", params={"$select": "id"})
            return True
        except GraphError as error:
            if error.status == 404:
                return False
            raise

    def create_event(self, calendar_id: str, event: dict[str, Any]) -> str:
        return str(self.request("POST", f"/me/calendars/{calendar_id}/events", event)["id"])

    def update_event(self, event_id: str, event: dict[str, Any]) -> None:
        self.request("PATCH", f"/me/events/{event_id}", event)

    def delete_event(self, event_id: str) -> None:
        try:
            self.request("DELETE", f"/me/events/{event_id}")
        except GraphError as error:
            if error.status != 404:
                raise

    # Mail

    def send_mail(self, *, to: str, subject: str, html: str, headers: dict[str, str]) -> None:
        message: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
        # Graph only accepts custom headers starting with X-; List-Unsubscribe can't be set through it.
        extra = [{"name": name, "value": value} for name, value in headers.items() if name.lower().startswith("x-")]
        if extra:
            message["internetMessageHeaders"] = extra
        self.request("POST", "/me/sendMail", {"message": message, "saveToSentItems": False})
