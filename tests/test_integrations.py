import hashlib
import hmac
import json
import time
from collections.abc import Callable, Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app
from app.models.integration import (
    CalendarSyncEvent,
    ConnectionStatus,
    IntegrationConnection,
    IntegrationDestination,
    IntegrationMessage,
    IntegrationUserLink,
    MicrosoftCalendarConnection,
)
from app.models.user import User
from app.routers.integrations import OAUTH_COOKIE, get_integrations_http
from app.services.email_sender import NoEmailRoute, OutgoingEmail
from app.services.integrations import secrets as integration_secrets
from app.services.integrations.calendar import CalendarSyncService, render_ics, CalendarItem
from app.services.integrations.delivery import IntegrationDelivery
from app.services.integrations.events import EventScanner
from app.services.integrations.http import HttpResponse
from app.services.integrations.mail import RoutingEmailSender
from app.services.integrations.secrets import TokenCipher, sign, unsign
from tests.ai_support import acting_as, add_member, create_account, db_session, dev_user, post  # noqa: F401

APP = "https://app.example.com"
SIGNING = "slack-signing-secret"


class FakeHttp:
    """Answers integration HTTP calls from handlers registered per (method, URL prefix); records every call."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Callable[[dict[str, Any]], HttpResponse]]] = []
        self.calls: list[dict[str, Any]] = []

    def on(self, method: str, prefix: str, handler: Callable[[dict[str, Any]], HttpResponse] | dict | int) -> None:
        if isinstance(handler, dict):
            body = handler
            handler = lambda call, body=body: HttpResponse(200, body)  # noqa: E731
        elif isinstance(handler, int):
            code = handler
            handler = lambda call, code=code: HttpResponse(code, {})  # noqa: E731
        self.routes.insert(0, (method, prefix, handler))

    def __call__(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        for route_method, prefix, handler in self.routes:
            if route_method == method and url.startswith(prefix):
                return handler(call)
        raise AssertionError(f"Unexpected {method} {url}")

    def sent(self, prefix: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["url"].startswith(prefix)]


@pytest.fixture()
def settings() -> Settings:
    integration_secrets.forget_cached_secrets()
    return Settings(
        auth_mode="local",
        environment="local",
        app_url=APP,
        integration_local_secret="test-integrations",
        integration_state_secret="test-state",
        slack_client_id="slack-client",
        slack_client_secret="slack-secret",
        slack_signing_secret=SIGNING,
        microsoft_client_id="ms-client",
        microsoft_client_secret="ms-secret",
        email_token_secret="email-secret",
    )


@pytest.fixture()
def http() -> FakeHttp:
    fake = FakeHttp()
    return fake


@pytest.fixture()
def client(db_session: Session, settings: Settings, http: FakeHttp) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_integrations_http] = lambda: http
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def account(client: TestClient) -> dict:
    hierarchy = create_account(client)
    yesterday = str(date.today() - timedelta(days=1))
    hierarchy["late"] = post(client, f"/api/v1/projects/{hierarchy['apollo']['id']}/tasks", {"name": "Late report", "finish_date": yesterday})
    return hierarchy


def account_path(account: dict, path: str = "") -> str:
    return f"/api/v1/accounts/{account['account']['id']}/integrations{path}"


def connect_slack(client: TestClient, http: FakeHttp, account: dict) -> None:
    url = client.get(account_path(account, "/slack/install")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    http.on("POST", "https://slack.com/api/oauth.v2.access", {"ok": True, "access_token": "xoxb-1", "team": {"id": "T1", "name": "Acme"}, "bot_user_id": "B1"})
    response = client.get("/api/v1/integrations/slack/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("&connected=slack"), response.headers["location"]


def slack_signed(body: str, *, secret: str = SIGNING, timestamp: int | None = None) -> dict[str, str]:
    stamp = str(timestamp or int(time.time()))
    signature = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature, "Content-Type": "application/x-www-form-urlencoded"}


def slash(client: TestClient, text: str, user: str = "U1") -> dict:
    body = urlencode({"team_id": "T1", "user_id": user, "user_name": "alex", "text": text, "command": "/projecthub"})
    response = client.post("/api/v1/integrations/slack/commands", content=body, headers=slack_signed(body))
    assert response.status_code == 200, response.text
    return response.json()


def link_token(reply: dict) -> str:
    url = next(b for b in reply["blocks"] if b["type"] == "actions")["elements"][0]["url"]
    return parse_qs(urlparse(url).query)["link"][0]


def run_worker(db: Session, settings: Settings, http: FakeHttp) -> dict[str, int]:
    queued = EventScanner(db).scan()
    counts = IntegrationDelivery(db, settings, http=http).deliver_pending()
    return {**counts, "queued": queued}


# Slack ----------------------------------------------------------------------------------------------------


def test_owner_installs_slack_and_the_token_is_stored_encrypted(client: TestClient, db_session: Session, http: FakeHttp, account: dict) -> None:
    connect_slack(client, http, account)
    connection = db_session.scalars(select(IntegrationConnection)).one()
    assert (connection.provider, connection.external_id, connection.external_name) == ("SLACK", "T1", "Acme")
    assert b"xoxb-1" not in connection.encrypted_token
    assert TokenCipher(get_settings_override()).decrypt(connection.account_id, connection.encrypted_token) == "xoxb-1"
    overview = client.get(account_path(account)).json()
    assert overview["connections"][0]["external_name"] == "Acme" and overview["available"]["slack"]


def get_settings_override() -> Settings:
    return app.dependency_overrides[get_settings]()


def test_slack_callback_needs_the_browser_that_started_it(client: TestClient, http: FakeHttp, account: dict) -> None:
    url = client.get(account_path(account, "/slack/install")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    client.cookies.clear()
    response = client.get("/api/v1/integrations/slack/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "integration_error=" in response.headers["location"]
    assert http.sent("https://slack.com/api/oauth.v2.access") == []


def test_only_owners_manage_integrations(client: TestClient, db_session: Session, account: dict) -> None:
    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS"])
    with acting_as(alex):
        assert client.get(account_path(account)).status_code == 403
        assert client.get(account_path(account, "/slack/install")).status_code == 403
        assert client.get(account_path(account, "/me")).status_code == 200


def add_destination(client: TestClient, account: dict, events: list[str], *, scope: str = "apollo", scope_type: str = "PROJECT") -> dict:
    response = client.post(
        account_path(account, "/destinations"),
        json={"provider": "slack", "channel_id": "C1", "channel_name": "apollo", "scope_type": scope_type, "scope_id": account[scope]["id"], "events": events},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_channel_gets_events_for_its_project_only(client: TestClient, db_session: Session, settings: Settings, http: FakeHttp, account: dict) -> None:
    connect_slack(client, http, account)
    http.on("POST", "https://slack.com/api/conversations.list", {"ok": True, "channels": [{"id": "C1", "name": "apollo"}, {"id": "C2", "name": "secret", "is_private": True, "is_member": False}]})
    assert [c["name"] for c in client.get(account_path(account, "/slack/channels")).json()] == ["apollo"]
    add_destination(client, account, ["task_created", "risk_created_high"])
    EventScanner(db_session).scan()  # start the cursor after setup

    post(client, f"/api/v1/projects/{account['apollo']['id']}/tasks", {"name": "Draft launch plan"})
    post(client, f"/api/v1/projects/{account['secret']['id']}/tasks", {"name": "Hidden work"})
    post(client, f"/api/v1/projects/{account['apollo']['id']}/risks", {"title": "Low risk"})
    http.on("POST", "https://slack.com/api/chat.postMessage", {"ok": True})

    counts = run_worker(db_session, settings, http)

    assert counts["queued"] == 1 and counts["sent"] == 1
    message = http.sent("https://slack.com/api/chat.postMessage")[0]
    assert message["headers"]["Authorization"] == "Bearer xoxb-1" and message["json"]["channel"] == "C1"
    assert "Draft launch plan" in message["json"]["text"] and f"{APP}/projects/" in json.dumps(message["json"]["blocks"])
    # Scanning again doesn't post twice.
    assert run_worker(db_session, settings, http)["queued"] == 0


def test_events_are_checked_against_whoever_set_up_the_channel(client: TestClient, db_session: Session, settings: Settings, http: FakeHttp, account: dict) -> None:
    connect_slack(client, http, account)
    destination = add_destination(client, account, ["task_created"], scope="secret")
    EventScanner(db_session).scan()
    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS"])  # Apollo only
    row = db_session.get(IntegrationDestination, UUID(destination["id"]))
    row.created_by = alex.id
    db_session.commit()

    post(client, f"/api/v1/projects/{account['secret']['id']}/tasks", {"name": "Hidden work"})
    counts = run_worker(db_session, settings, http)

    assert (counts["queued"], counts["skipped"], counts["sent"]) == (1, 1, 0)
    assert http.sent("https://slack.com/api/chat.postMessage") == []


def test_a_revoked_slack_token_marks_the_connection(client: TestClient, db_session: Session, settings: Settings, http: FakeHttp, account: dict) -> None:
    connect_slack(client, http, account)
    add_destination(client, account, ["task_created"])
    EventScanner(db_session).scan()
    post(client, f"/api/v1/projects/{account['apollo']['id']}/tasks", {"name": "Draft"})
    http.on("POST", "https://slack.com/api/chat.postMessage", {"ok": False, "error": "token_revoked"})

    assert run_worker(db_session, settings, http)["failed"] == 1
    connection = db_session.scalars(select(IntegrationConnection)).one()
    assert connection.status == ConnectionStatus.ERROR.value and "token_revoked" in connection.last_error


def test_slack_commands_need_a_valid_signature(client: TestClient, http: FakeHttp, account: dict) -> None:
    connect_slack(client, http, account)
    body = urlencode({"team_id": "T1", "user_id": "U1", "text": "mine"})
    assert client.post("/api/v1/integrations/slack/commands", content=body, headers=slack_signed(body, secret="wrong")).status_code == 401
    stale = slack_signed(body, timestamp=int(time.time()) - 600)
    assert client.post("/api/v1/integrations/slack/commands", content=body, headers=stale).status_code == 401


def test_slack_user_links_then_uses_commands_and_gets_direct_messages(client: TestClient, db_session: Session, settings: Settings, http: FakeHttp, account: dict) -> None:
    connect_slack(client, http, account)
    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS"])
    assert "Link your Project Hub account first" in slash(client, "mine")["text"]

    token = link_token(slash(client, "link"))
    with acting_as(alex):
        linked = client.post(account_path(account, "/me/links/confirm"), json={"token": token})
        assert linked.status_code == 200, linked.text
        assert client.get(account_path(account, "/me")).json()["slack"]["link"]["dm_categories"] == ["mentions", "assignments"]

    created = slash(client, "new Draft the plan in apollo")
    assert "Added “Draft the plan” to Apollo" in created["text"]
    assert "Nothing found" in slash(client, "search zzzz")["text"]

    # Assigning a task to Alex becomes a Slack direct message.
    task = post(client, f"/api/v1/projects/{account['apollo']['id']}/tasks", {"name": "Review budget"})
    assert client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(alex.id)}).status_code in (200, 201)
    http.on("POST", "https://slack.com/api/conversations.open", {"ok": True, "channel": {"id": "D1"}})
    http.on("POST", "https://slack.com/api/chat.postMessage", {"ok": True})
    counts = run_worker(db_session, settings, http)
    assert counts["sent"] == 1
    dm = http.sent("https://slack.com/api/chat.postMessage")[0]["json"]
    assert dm["channel"] == "D1" and "Review budget" in dm["text"]


def test_link_tokens_are_signed_and_expire(settings: Settings) -> None:
    token = sign(settings, "user-link", {"x": "U1"}, ttl_seconds=60)
    assert unsign(settings, "user-link", token) == {"x": "U1"}
    assert unsign(settings, "slack-install", token) is None
    assert unsign(settings, "user-link", token[:-2] + "xx") is None
    assert unsign(settings, "user-link", sign(settings, "user-link", {}, ttl_seconds=-1)) is None


# Calendars ------------------------------------------------------------------------------------------------


def test_calendar_feed_lists_my_tasks_and_can_be_replaced(client: TestClient, db_session: Session, account: dict) -> None:
    me = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS"])
    due = date.today() + timedelta(days=3)
    task = post(client, f"/api/v1/projects/{account['apollo']['id']}/tasks", {"name": "Board pack", "finish_date": str(due)})
    client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(alex.id)})
    with acting_as(alex):
        feed = client.post("/api/v1/me/calendar-feed").json()
        path = urlparse(feed["url"]).path
        ics = client.get(path)
        assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
        assert "SUMMARY:Board pack (Apollo)" in ics.text and f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}" in ics.text
        assert "Late report" not in ics.text  # not assigned to Alex
        new_url = client.post("/api/v1/me/calendar-feed").json()["url"]
    assert new_url != feed["url"] and client.get(path).status_code == 404
    assert me is not None


def test_ics_escapes_and_folds_long_lines() -> None:
    item = CalendarItem(key="task:1", title="A, B; C " + "x" * 120, day=date(2026, 10, 20), path="/p", detail="d")
    text = render_ics(Settings(app_url=APP), [item], now=datetime(2026, 10, 13, tzinfo=UTC))
    assert "SUMMARY:A\\, B\\; C" in text and "\r\n " in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def graph(http: FakeHttp) -> dict[str, Any]:
    store: dict[str, Any] = {"events": {}, "next": 0}

    def create_event(call: dict[str, Any]) -> HttpResponse:
        store["next"] += 1
        event_id = f"e{store['next']}"
        store["events"][event_id] = call["json"]
        return HttpResponse(201, {"id": event_id})

    def patch_event(call: dict[str, Any]) -> HttpResponse:
        store["events"][call["url"].rsplit("/", 1)[-1]] = call["json"]
        return HttpResponse(200, {})

    http.on("POST", "https://login.microsoftonline.com/common/oauth2/v2.0/token", {"access_token": "graph-token", "refresh_token": "refresh-2"})
    http.on("GET", "https://graph.microsoft.com/v1.0/me", {"id": "ms-alex", "mail": "alex@contoso.com"})
    http.on("POST", "https://graph.microsoft.com/v1.0/me/calendars", lambda call: HttpResponse(201, {"id": "cal-1"}))
    http.on("GET", "https://graph.microsoft.com/v1.0/me/calendars/cal-1", {"id": "cal-1"})
    http.on("POST", "https://graph.microsoft.com/v1.0/me/calendars/cal-1/events", create_event)
    http.on("PATCH", "https://graph.microsoft.com/v1.0/me/events/", patch_event)
    http.on("POST", "https://graph.microsoft.com/v1.0/me/sendMail", 202)
    return store


def test_outlook_calendar_sync_writes_and_updates_events(client: TestClient, db_session: Session, settings: Settings, http: FakeHttp, account: dict) -> None:
    store = graph(http)
    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS"])
    task = post(client, f"/api/v1/projects/{account['apollo']['id']}/tasks", {"name": "Board pack", "finish_date": str(date.today() + timedelta(days=3))})
    client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(alex.id)})

    with acting_as(alex):
        url = client.get("/api/v1/me/outlook-calendar/connect").json()["url"]
        state = parse_qs(urlparse(url).query)["state"][0]
        response = client.get("/api/v1/integrations/microsoft/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert response.headers["location"].endswith("tab=connected&connected=outlook"), response.headers["location"]
    connection = db_session.get(MicrosoftCalendarConnection, alex.id)
    assert connection.calendar_id == "cal-1" and connection.email == "alex@contoso.com"
    (event,) = store["events"].values()
    assert event["subject"] == "Board pack (Apollo)" and event["isAllDay"] is True

    # Nothing changed: nothing written. A new date: the event is updated.
    CalendarSyncService(db_session, settings, http=http).sync(alex.id)
    assert len(http.sent("https://graph.microsoft.com/v1.0/me/events/")) == 0
    new_date = date.today() + timedelta(days=5)
    assert client.patch(f"/api/v1/tasks/{task['id']}", json={"finish_date": str(new_date)}).status_code == 200
    assert CalendarSyncService(db_session, settings, http=http).sync(alex.id)
    assert store["events"]["e1"]["start"]["dateTime"].startswith(new_date.isoformat())
    assert db_session.scalars(select(CalendarSyncEvent)).one().event_id == "e1"


# Microsoft 365 mail ---------------------------------------------------------------------------------------


class FakeSes:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    def send(self, email: OutgoingEmail) -> None:
        self.sent.append(email)


def test_account_mailbox_sends_its_email_and_ses_covers_the_rest(client: TestClient, db_session: Session, settings: Settings, http: FakeHttp, account: dict) -> None:
    graph(http)
    url = client.get(account_path(account, "/microsoft-mail/connect")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    response = client.get("/api/v1/integrations/microsoft/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert response.headers["location"].endswith("&connected=microsoft-mail")
    account_id = UUID(account["account"]["id"])

    ses = FakeSes()
    sender = RoutingEmailSender(db_session, settings, ses, http=http)
    sender.send(OutgoingEmail(to="a@example.com", subject="Hi", text="t", html="<p>t</p>", account_id=account_id))
    mail = http.sent("https://graph.microsoft.com/v1.0/me/sendMail")[0]["json"]["message"]
    assert mail["toRecipients"][0]["emailAddress"]["address"] == "a@example.com" and ses.sent == []

    sender.send(OutgoingEmail(to="b@example.com", subject="Hi", text="t", html="t"))
    assert [e.to for e in ses.sent] == ["b@example.com"]
    with pytest.raises(NoEmailRoute):
        RoutingEmailSender(db_session, settings, None, http=http).send(OutgoingEmail(to="c@example.com", subject="x", text="t", html="t"))


def test_a_connected_mailbox_turns_on_email_for_its_account(
    client: TestClient, db_session: Session, settings: Settings, http: FakeHttp, account: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.notifications as notifications

    monkeypatch.setattr(notifications, "get_settings", lambda: settings)
    graph(http)
    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS"])
    task = post(client, f"/api/v1/projects/{account['apollo']['id']}/tasks", {"name": "First"})
    client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(alex.id)})
    from app.models.notification import Notification

    assert db_session.scalars(select(Notification)).one().email_status is None

    url = client.get(account_path(account, "/microsoft-mail/connect")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    client.get("/api/v1/integrations/microsoft/callback", params={"code": "c", "state": state}, follow_redirects=False)
    second = post(client, f"/api/v1/projects/{account['apollo']['id']}/tasks", {"name": "Second"})
    client.post(f"/api/v1/tasks/{second['id']}/assignments", json={"user_id": str(alex.id)})
    statuses = [n.email_status for n in db_session.scalars(select(Notification).order_by(Notification.created_at)).all()]
    assert statuses[-1] == "PENDING"
    assert db_session.scalars(select(IntegrationMessage)).all() == []
