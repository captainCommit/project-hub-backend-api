"""Slack's Web API, the OAuth install, and checking that requests really come from Slack.

One Slack app serves every account. Installing it into a workspace (OAuth v2) gives a bot token for that
workspace, stored encrypted on the account's connection. Slash commands and events are signed with the app's
signing secret, which every incoming request is checked against.
"""

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

from app.core.config import Settings
from app.services.integrations.http import Http
from app.services.integrations.secrets import require_secret

API = "https://slack.com/api/"
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
# Post to channels (public ones without being invited), list channels for the picker, open DMs, read users.
BOT_SCOPES = "chat:write,chat:write.public,channels:read,groups:read,im:write,users:read,commands"
# Errors meaning the token no longer works: the connection needs an owner to connect again.
AUTH_ERRORS = {"invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired", "missing_scope", "team_access_not_granted"}
MAX_REQUEST_AGE_SECONDS = 300
CHANNEL_LIST_LIMIT = 1000


class SlackError(Exception):
    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error

    @property
    def is_auth(self) -> bool:
        return self.error in AUTH_ERRORS


def redirect_uri(settings: Settings) -> str:
    return f"{settings.app_url.rstrip('/')}/api/v1/integrations/slack/callback"


def install_url(settings: Settings, state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode(
        {"client_id": settings.slack_client_id, "scope": BOT_SCOPES, "state": state, "redirect_uri": redirect_uri(settings)}
    )


def exchange_code(settings: Settings, http: Http, code: str) -> dict[str, Any]:
    """oauth.v2.access: the workspace, the bot token and the bot's user id."""
    response = http(
        "POST",
        API + "oauth.v2.access",
        data={
            "client_id": settings.slack_client_id,
            "client_secret": require_secret(settings, "slack_client_secret"),
            "code": code,
            "redirect_uri": redirect_uri(settings),
        },
    )
    data = response.data if isinstance(response.data, dict) else {}
    if not data.get("ok"):
        raise SlackError(str(data.get("error") or f"http_{response.status}"))
    return data


def verify_request(settings: Settings, *, timestamp: str | None, signature: str | None, body: bytes, now: float | None = None) -> bool:
    """Slack's request signing: v0=HMAC-SHA256(signing secret, "v0:<timestamp>:<body>"), at most 5 minutes old."""
    if not timestamp or not signature:
        return False
    try:
        age = abs((now or time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > MAX_REQUEST_AGE_SECONDS:
        return False
    secret = require_secret(settings, "slack_signing_secret").encode()
    expected = "v0=" + hmac.new(secret, f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class SlackClient:
    def __init__(self, token: str, http: Http) -> None:
        self.token = token
        self.http = http

    def call(self, method: str, **payload: Any) -> dict[str, Any]:
        response = self.http(
            "POST",
            API + method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=utf-8"},
            json=payload,
        )
        data = response.data if isinstance(response.data, dict) else {}
        if response.status == 429:
            raise SlackError("ratelimited")
        if not data.get("ok"):
            raise SlackError(str(data.get("error") or f"http_{response.status}"))
        return data

    def post_message(self, channel: str, *, text: str, blocks: list[dict[str, Any]]) -> None:
        self.call("chat.postMessage", channel=channel, text=text[:3000], blocks=blocks, unfurl_links=False)

    def open_dm(self, user_id: str) -> str:
        return str(self.call("conversations.open", users=user_id)["channel"]["id"])

    def channels(self) -> list[dict[str, Any]]:
        """Channels the bot can post to: public ones, and private ones it has been invited to."""
        found: list[dict[str, Any]] = []
        cursor = None
        while len(found) < CHANNEL_LIST_LIMIT:
            payload: dict[str, Any] = {"types": "public_channel,private_channel", "exclude_archived": True, "limit": 200}
            if cursor:
                payload["cursor"] = cursor
            data = self.call("conversations.list", **payload)
            for channel in data.get("channels") or []:
                if channel.get("is_private") and not channel.get("is_member"):
                    continue
                found.append({"id": channel["id"], "name": channel.get("name") or channel["id"], "is_private": bool(channel.get("is_private"))})
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return sorted(found, key=lambda channel: channel["name"])

    def user_name(self, user_id: str) -> str | None:
        try:
            user = self.call("users.info", user=user_id).get("user") or {}
        except SlackError:
            return None
        profile = user.get("profile") or {}
        return profile.get("real_name") or user.get("real_name") or user.get("name")

    def revoke(self) -> None:
        self.call("auth.revoke")
