from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.access_grant import AccessGrant
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.email_suppression import EmailSuppression
from app.models.notification import Notification
from app.models.notification_preference import NotificationPreference
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.user import User
from app.services import notification_preferences as preferences_module
from app.services import notifications as notifications_module
from app.services.email_delivery import (
    MAX_SEND_ATTEMPTS,
    EmailDeliveryService,
    make_unsubscribe_token,
    read_unsubscribe_token,
)
from app.services.email_sender import OutgoingEmail, build_mime
from app.services.auth import get_current_user
from tests.helpers import grant_access

SECRET = "test-email-secret"
APP_URL = "https://app.example.com"


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.fail = False

    def send(self, email: OutgoingEmail) -> None:
        if self.fail:
            raise RuntimeError("SES is down")
        self.sent.append(email)


@pytest.fixture()
def email_settings() -> Settings:
    return Settings(
        auth_mode="local",
        email_from="Project Hub <notifications@example.com>",
        email_token_secret=SECRET,
        app_url=APP_URL,
    )


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session, email_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    monkeypatch.setattr(notifications_module, "get_settings", lambda: email_settings)
    monkeypatch.setattr(preferences_module, "get_settings", lambda: email_settings)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: email_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture()
def delivery(db_session: Session, email_settings: Settings, sender: FakeSender) -> EmailDeliveryService:
    return EmailDeliveryService(db_session, email_settings, sender)


def create_hierarchy(client: TestClient) -> dict[str, dict]:
    suffix = uuid4().hex[:8]
    account = client.post("/api/v1/accounts", json={"name": f"Acme {suffix}", "slug": f"acme-{suffix}"}).json()
    portfolio = client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "Portfolio"}).json()
    program = client.post(f"/api/v1/portfolios/{portfolio['id']}/programs", json={"name": "Program"}).json()
    project = client.post(f"/api/v1/programs/{program['id']}/projects", json={"name": "Apollo"}).json()
    return {"account": account, "portfolio": portfolio, "program": program, "project": project}


def create_task(client: TestClient, project_id: str, name: str = "Write launch plan", **fields: object) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": name, **fields})
    assert response.status_code == 201
    return response.json()


def create_member(db_session: Session, hierarchy: dict, email: str, *, grant: bool = True) -> User:
    user = User(email=email, full_name=email.split("@")[0].title())
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountMember(account_id=UUID(hierarchy["account"]["id"]), user_id=user.id, role=AccountMemberRole.MEMBER.value))
    db_session.commit()
    if grant:
        grant_access(
            db_session,
            account_id=hierarchy["account"]["id"],
            role="MEMBER",
            project_id=hierarchy["project"]["id"],
            modules=["TASKS", "RISKS", "DECISIONS"],
            email=email,
        )
    return user


def assign(client: TestClient, task_id: str, user: User) -> None:
    response = client.post(f"/api/v1/tasks/{task_id}/assignments", json={"user_id": str(user.id)})
    assert response.status_code == 201


def notifications_for(db_session: Session, user: User) -> list[Notification]:
    return list(db_session.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at)).all())


def set_preferences(client: TestClient, user: User, **changes: object) -> dict:
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = client.patch("/api/v1/me/notification-preferences", json=changes)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200, response.text
    return response.json()


# Queueing ----------------------------------------------------------------------------------------------------


def test_assignment_email_is_sent_with_link_to_the_task(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    member = create_member(db_session, hierarchy, "alex@example.com")

    assign(client, task["id"], member)
    [notification] = notifications_for(db_session, member)
    assert notification.email_status == "PENDING"

    counts = delivery.deliver_pending()

    assert counts["sent"] == 1
    [email] = sender.sent
    assert email.to == "alex@example.com"
    assert email.subject == "Task assigned to you: Write launch plan"
    task_url = f"{APP_URL}/projects/{hierarchy['project']['id']}/sheet?task={task['id']}"
    assert task_url in email.text
    assert task_url.replace("&", "&amp;") in email.html
    assert email.headers["List-Unsubscribe"].startswith(f"<{APP_URL}/api/v1/email/unsubscribe?token=")
    assert email.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    db_session.refresh(notification)
    assert notification.email_status == "SENT"
    assert notification.emailed_at is not None
    # Nothing left to send.
    assert delivery.deliver_pending()["sent"] == 0


def test_nothing_is_queued_when_email_is_not_configured(
    client: TestClient, db_session: Session, email_settings: Settings
) -> None:
    email_settings.email_from = ""
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    member = create_member(db_session, hierarchy, "alex@example.com")

    assign(client, task["id"], member)

    [notification] = notifications_for(db_session, member)
    assert notification.email_status is None


def test_preferences_decide_how_notifications_are_queued(client: TestClient, db_session: Session) -> None:
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    member = create_member(db_session, hierarchy, "alex@example.com")
    set_preferences(client, member, assignments="OFF")

    assign(client, task["id"], member)
    risk = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/risks",
        json={"title": "Vendor delay", "assigned_to": str(member.id)},
    )
    assert risk.status_code == 201
    set_preferences(client, member, mentions="DIGEST")
    client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": "Thoughts @alex@example.com?"})

    statuses = {n.notification_type: n.email_status for n in notifications_for(db_session, member)}
    assert statuses == {"TASK_ASSIGNED": None, "RISK_CREATED": None, "MENTION": "DIGEST", "COMMENT_ADDED": None}


def test_mention_email_links_to_the_commented_task(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Budget review")
    member = create_member(db_session, hierarchy, "sam@example.com")

    client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": "Can @sam@example.com check <this>?"})
    delivery.deliver_pending()

    [email] = sender.sent
    assert email.subject == "You were mentioned: Budget review"
    assert f"/projects/{hierarchy['project']['id']}/sheet?task={task['id']}" in email.text
    assert "<this>" not in email.html  # user text is escaped
    assert "unsubscribe" in email.text.lower()


# Send-time checks --------------------------------------------------------------------------------------------


def test_email_is_skipped_when_access_was_removed(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    member = create_member(db_session, hierarchy, "alex@example.com")
    assign(client, task["id"], member)

    db_session.execute(delete(AccessGrant).where(AccessGrant.user_id == member.id))
    db_session.commit()
    counts = delivery.deliver_pending()

    assert counts["skipped"] == 1
    assert sender.sent == []
    assert notifications_for(db_session, member)[0].email_status == "SKIPPED"


def test_email_follows_settings_changed_after_queueing(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    first = create_task(client, hierarchy["project"]["id"], "First")
    second = create_task(client, hierarchy["project"]["id"], "Second")
    assign(client, first["id"], member)
    set_preferences(client, member, assignments="OFF")
    delivery.deliver_pending()
    assert sender.sent == []

    set_preferences(client, member, assignments="IMMEDIATE")
    assign(client, second["id"], member)
    set_preferences(client, member, assignments="DIGEST")
    counts = delivery.deliver_pending()

    assert counts["digest"] == 1
    assert [n.email_status for n in notifications_for(db_session, member)] == ["SKIPPED", "DIGEST"]


def test_suppressed_address_is_not_emailed(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    member = create_member(db_session, hierarchy, "Bounced@Example.com")
    db_session.add(EmailSuppression(email="bounced@example.com", reason="BOUNCE"))
    db_session.commit()

    assign(client, task["id"], member)
    delivery.deliver_pending()

    assert sender.sent == []
    assert notifications_for(db_session, member)[0].email_status == "SKIPPED"


def test_failed_sends_are_retried_then_given_up(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    member = create_member(db_session, hierarchy, "alex@example.com")
    assign(client, task["id"], member)
    sender.fail = True

    assert delivery.deliver_pending()["retry"] == 1
    [notification] = notifications_for(db_session, member)
    assert (notification.email_status, notification.email_attempts) == ("PENDING", 1)

    for _ in range(MAX_SEND_ATTEMPTS - 1):
        delivery.deliver_pending()
    db_session.refresh(notification)
    assert (notification.email_status, notification.email_attempts) == ("FAILED", MAX_SEND_ATTEMPTS)


def test_stale_claims_are_picked_up_again(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    member = create_member(db_session, hierarchy, "alex@example.com")
    assign(client, task["id"], member)
    [notification] = notifications_for(db_session, member)
    now = datetime.now(UTC)
    notification.email_status = "SENDING"
    notification.email_claimed_at = now - timedelta(minutes=1)
    db_session.commit()

    assert delivery.deliver_pending(now=now)["sent"] == 0  # another run is still on it
    assert delivery.deliver_pending(now=now + timedelta(minutes=15))["sent"] == 1


# Preferences API ---------------------------------------------------------------------------------------------


def test_preferences_default_and_update(client: TestClient, db_session: Session) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    app.dependency_overrides[get_current_user] = lambda: member

    defaults = client.get("/api/v1/me/notification-preferences")
    updated = client.patch(
        "/api/v1/me/notification-preferences",
        json={"comments": "DIGEST", "timezone": "Europe/London", "digest_hour": 7},
    )
    bad_zone = client.patch("/api/v1/me/notification-preferences", json={"timezone": "Mars/Base"})
    bad_value = client.patch("/api/v1/me/notification-preferences", json={"mentions": "SOMETIMES"})
    bad_hour = client.patch("/api/v1/me/notification-preferences", json={"digest_hour": 24})

    assert defaults.status_code == 200
    assert defaults.json() == {
        "mentions": "IMMEDIATE",
        "assignments": "IMMEDIATE",
        "due_soon": "DIGEST",
        "status_changes": "DIGEST",
        "comments": "OFF",
        "timezone": "America/Toronto",
        "digest_hour": 8,
        "timezone_confirmed": False,
        "my_work_summary": True,
        "email": "alex@example.com",
        "email_available": True,
        "email_suppressed": False,
    }
    assert updated.status_code == 200
    assert (updated.json()["comments"], updated.json()["timezone"], updated.json()["digest_hour"]) == ("DIGEST", "Europe/London", 7)
    assert updated.json()["mentions"] == "IMMEDIATE"
    assert updated.json()["timezone_confirmed"] is True
    assert bad_zone.status_code == 400
    assert bad_value.status_code == 422
    assert bad_hour.status_code == 422


# Unsubscribe -------------------------------------------------------------------------------------------------


def test_unsubscribe_tokens_are_signed() -> None:
    user_id = uuid4()
    token = make_unsubscribe_token(SECRET, user_id, "mentions")

    assert read_unsubscribe_token(SECRET, token) == (user_id, "mentions")
    assert read_unsubscribe_token("other-secret", token) is None
    assert read_unsubscribe_token(SECRET, token[:-2] + "xx") is None
    forged = make_unsubscribe_token(SECRET, user_id, "mentions").split(".")[1]
    assert read_unsubscribe_token(SECRET, f"{token.split('.')[0]}x.{forged}") is None
    assert read_unsubscribe_token(SECRET, make_unsubscribe_token(SECRET, user_id, "nonsense")) is None
    assert read_unsubscribe_token(SECRET, "garbage") is None


def test_unsubscribe_page_asks_then_turns_off_the_category(client: TestClient, db_session: Session) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    token = make_unsubscribe_token(SECRET, member.id, "mentions")

    page = client.get("/api/v1/email/unsubscribe", params={"token": token})
    assert page.status_code == 200
    assert '<form method="post">' in page.text
    assert db_session.get(NotificationPreference, member.id) is None  # opening the link changes nothing

    done = client.post("/api/v1/email/unsubscribe", params={"token": token}, data={"List-Unsubscribe": "One-Click"})
    assert done.status_code == 200
    assert "unsubscribed" in done.text
    preference = db_session.get(NotificationPreference, member.id)
    assert preference is not None
    assert (preference.mentions, preference.assignments) == ("OFF", "IMMEDIATE")


def test_unsubscribe_all_and_bad_links(client: TestClient, db_session: Session) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")

    done = client.post("/api/v1/email/unsubscribe", params={"token": make_unsubscribe_token(SECRET, member.id, "all")})
    bad = client.post("/api/v1/email/unsubscribe", params={"token": "nope"})
    unknown_user = client.get("/api/v1/email/unsubscribe", params={"token": make_unsubscribe_token(SECRET, uuid4(), "all")})

    assert done.status_code == 200
    preference = db_session.get(NotificationPreference, member.id)
    assert {preference.mentions, preference.assignments, preference.due_soon, preference.status_changes, preference.comments} == {"OFF"}
    assert bad.status_code == 400
    assert unknown_user.status_code == 400


# Morning run: due soon + digest --------------------------------------------------------------------------------

# 13:00 UTC is 09:00 in Toronto (EDT), after the default 08:00 digest hour.
MORNING = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)
LOCAL_TODAY = date(2026, 10, 12)


def test_due_soon_reminders_are_created_once_in_the_local_morning(
    client: TestClient, db_session: Session, delivery, sender
) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    tomorrow = create_task(client, hierarchy["project"]["id"], "Ship it", finish_date=str(LOCAL_TODAY + timedelta(days=1)))
    later = create_task(client, hierarchy["project"]["id"], "Later", finish_date=str(LOCAL_TODAY + timedelta(days=5)))
    unassigned = create_task(client, hierarchy["project"]["id"], "Not mine", finish_date=str(LOCAL_TODAY))
    assign(client, tomorrow["id"], member)
    assign(client, later["id"], member)
    db_session.execute(delete(Notification))
    db_session.commit()

    too_early = delivery.run_morning(now=MORNING - timedelta(hours=2))  # 07:00 in Toronto
    assert too_early["due_soon"] == 0

    delivery.run_morning(now=MORNING)
    delivery.run_morning(now=MORNING + timedelta(hours=1))  # later the same day: nothing new

    [reminder] = notifications_for(db_session, member)
    assert (reminder.notification_type, reminder.entity_id, reminder.title) == ("DUE_SOON", UUID(tomorrow["id"]), "Task due tomorrow")
    assert reminder.message == "Ship it (Apollo) is due tomorrow."
    assert reminder.email_status == "SENT"  # in that morning's digest (the default for due-soon)
    assert unassigned["id"] not in {str(n.entity_id) for n in db_session.scalars(select(Notification))}
    assert db_session.get(NotificationPreference, member.id).last_due_soon_on == LOCAL_TODAY


def test_digest_collects_queued_notifications(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    set_preferences(client, member, assignments="DIGEST")
    for name in ["Alpha", "Beta"]:
        assign(client, create_task(client, hierarchy["project"]["id"], name)["id"], member)

    assert delivery.deliver_pending()["sent"] == 0
    counts = delivery.run_morning(now=MORNING)
    delivery.run_morning(now=MORNING + timedelta(hours=1))

    assert counts["digests"] == 1
    [digest] = sender.sent
    assert digest.subject == "Your Project Hub digest: 2 updates"
    assert "Task assigned to you: Alpha" in digest.text and "Task assigned to you: Beta" in digest.text
    assert {n.email_status for n in notifications_for(db_session, member)} == {"SENT"}
    assert db_session.get(NotificationPreference, member.id).last_digest_on == LOCAL_TODAY


def test_no_digest_when_nothing_is_queued(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    create_member(db_session, hierarchy, "alex@example.com")

    counts = delivery.run_morning(now=MORNING)

    assert counts["digests"] == 0
    assert sender.sent == []


def test_failed_digest_is_retried_next_hour(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    set_preferences(client, member, assignments="DIGEST")
    assign(client, create_task(client, hierarchy["project"]["id"])["id"], member)

    sender.fail = True
    delivery.run_morning(now=MORNING)
    sender.fail = False
    delivery.run_morning(now=MORNING + timedelta(hours=1))

    assert len(sender.sent) == 1


# Daily digest: My work summary (IN-2) --------------------------------------------------------------------------


def task_status_id(db_session: Session, hierarchy: dict, value: str) -> str:
    option_id = db_session.scalar(
        select(OptionValue.id)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(hierarchy["account"]["id"]),
            OptionSet.entity_type == "TASK",
            OptionSet.name == "STATUS",
            OptionValue.value == value,
        )
    )
    assert option_id is not None
    return str(option_id)


def due_in(days: int) -> str:
    return str(LOCAL_TODAY + timedelta(days=days))


def test_digest_summarizes_my_work(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    member = create_member(db_session, hierarchy, "alex@example.com")
    blocked = task_status_id(db_session, hierarchy, "BLOCKED")
    mine = {
        "Late report": create_task(client, project_id, "Late report", finish_date=due_in(-3), status_id=blocked),
        "Standup notes": create_task(client, project_id, "Standup notes", finish_date=due_in(0)),
        "Vendor contract": create_task(client, project_id, "Vendor contract", finish_date=due_in(5), status_id=blocked),
        "Next week": create_task(client, project_id, "Next week", finish_date=due_in(7)),
        "Already done": create_task(client, project_id, "Already done", finish_date=due_in(-1), percent_complete=100),
    }
    for task in mine.values():
        assign(client, task["id"], member)
    create_task(client, project_id, "Someone else's", finish_date=due_in(-1))
    client.post(f"/api/v1/entities/TASK/{mine['Next week']['id']}/comments", json={"body": "@alex@example.com thoughts?"})

    counts = delivery.run_morning(now=MORNING)

    assert counts["digests"] == 1
    [digest] = [email for email in sender.sent if email.subject.startswith("Your Project Hub digest")]
    assert digest.subject == "Your Project Hub digest: 1 overdue, 1 due today, 1 blocked, 1 new mention"
    text = digest.text
    assert text.index("OVERDUE (1)") < text.index("Late report") < text.index("DUE TODAY (1)") < text.index("Standup notes")
    assert "Apollo · 3 days overdue · Blocked" in text
    assert text.index("BLOCKED (1)") < text.index("Vendor contract") < text.index("NEW MENTIONS (1)")
    assert text.index("NEW MENTIONS (1)") < text.index("Next week")  # mentioned on, but not due yet
    assert f"{APP_URL}/projects/{project_id}/sheet?task={mine['Late report']['id']}" in text
    assert "Already done" not in text and "Someone else's" not in text
    assert "Open My work" in digest.html
    token = text.split("Stop the My work summary: ")[1].split()[0].split("token=")[1]
    assert read_unsubscribe_token(SECRET, token) == (member.id, "my_work_summary")
    assert db_session.get(NotificationPreference, member.id).last_digest_on == LOCAL_TODAY


def test_digest_combines_my_work_with_queued_updates(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    set_preferences(client, member, assignments="DIGEST")
    assign(client, create_task(client, hierarchy["project"]["id"], "Overdue thing", finish_date=due_in(-1))["id"], member)
    delivery.deliver_pending()

    delivery.run_morning(now=MORNING)

    [digest] = sender.sent
    assert digest.subject == "Your Project Hub digest: 1 overdue, 1 update"
    assert digest.text.index("OVERDUE (1)") < digest.text.index("OTHER UPDATES (1)")
    assert "Task assigned to you: Overdue thing" in digest.text


def test_due_soon_reminders_are_not_repeated_next_to_the_summary(
    client: TestClient, db_session: Session, delivery, sender
) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    for name, days in [("Today's task", 0), ("Tomorrow's task", 1)]:
        assign(client, create_task(client, hierarchy["project"]["id"], name, finish_date=due_in(days))["id"], member)
    delivery.deliver_pending()  # assignment emails

    delivery.run_morning(now=MORNING)

    digest = sender.sent[-1]
    assert digest.subject == "Your Project Hub digest: 1 due today, 1 update"
    assert digest.text.count("Today's task") == 1
    assert "Task due tomorrow: Tomorrow's task" in digest.text
    reminders = [n for n in notifications_for(db_session, member) if n.notification_type == "DUE_SOON"]
    assert len(reminders) == 2 and {n.email_status for n in reminders} == {"SENT"}


def test_my_work_summary_respects_project_access(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    hidden_project = client.post(f"/api/v1/programs/{hierarchy['program']['id']}/projects", json={"name": "Secret"}).json()
    hidden = create_task(client, hidden_project["id"], "Hidden task", finish_date=due_in(0))
    assign(client, hidden["id"], member)

    counts = delivery.run_morning(now=MORNING)

    assert counts["digests"] == 0
    assert sender.sent == []


def test_my_work_summary_can_be_turned_off(client: TestClient, db_session: Session, delivery, sender) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    set_preferences(client, member, due_soon="OFF")
    assign(client, create_task(client, hierarchy["project"]["id"], "Overdue", finish_date=due_in(-2))["id"], member)

    assert set_preferences(client, member, my_work_summary=False)["my_work_summary"] is False
    counts = delivery.run_morning(now=MORNING)

    assert counts["digests"] == 0
    assert sender.sent == []


def test_my_work_summary_unsubscribe_links(client: TestClient, db_session: Session) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")
    other = create_member(db_session, hierarchy, "sam@example.com")

    page = client.get("/api/v1/email/unsubscribe", params={"token": make_unsubscribe_token(SECRET, member.id, "my_work_summary")})
    client.post("/api/v1/email/unsubscribe", params={"token": make_unsubscribe_token(SECRET, member.id, "my_work_summary")})
    client.post("/api/v1/email/unsubscribe", params={"token": make_unsubscribe_token(SECRET, other.id, "all")})

    assert "your daily My work summary" in page.text
    preference = db_session.get(NotificationPreference, member.id)
    assert (preference.my_work_summary, preference.mentions) == (False, "IMMEDIATE")
    assert db_session.get(NotificationPreference, other.id).my_work_summary is False


def test_timezone_is_confirmed_once_set(client: TestClient, db_session: Session) -> None:
    hierarchy = create_hierarchy(client)
    member = create_member(db_session, hierarchy, "alex@example.com")

    assert set_preferences(client, member, digest_hour=7)["timezone_confirmed"] is False
    assert set_preferences(client, member, timezone="America/Vancouver")["timezone_confirmed"] is True


# SES bounce / complaint events ---------------------------------------------------------------------------------


def test_ses_events_suppress_hard_bounces_and_complaints(db_session: Session, delivery) -> None:
    hard = {"eventType": "Bounce", "bounce": {"bounceType": "Permanent", "bounceSubType": "General", "bouncedRecipients": [{"emailAddress": "Gone@Example.com"}]}}
    soft = {"eventType": "Bounce", "bounce": {"bounceType": "Transient", "bouncedRecipients": [{"emailAddress": "full@example.com"}]}}
    complaint = {"notificationType": "Complaint", "complaint": {"complainedRecipients": [{"emailAddress": "angry@example.com"}]}}

    assert delivery.record_ses_event(hard) == 1
    assert delivery.record_ses_event(hard) == 0
    assert delivery.record_ses_event(soft) == 0
    assert delivery.record_ses_event(complaint) == 1
    assert delivery.record_ses_event({"eventType": "Delivery"}) == 0

    assert {(s.email, s.reason) for s in db_session.scalars(select(EmailSuppression))} == {
        ("gone@example.com", "BOUNCE"),
        ("angry@example.com", "COMPLAINT"),
    }


def test_mime_message_keeps_subject_on_one_line() -> None:
    raw = build_mime(
        OutgoingEmail(to="a@example.com", subject="Hello\r\nBcc: evil@example.com", text="hi", html="<p>hi</p>"),
        "Project Hub <notifications@example.com>",
    ).decode()

    assert "\nBcc:" not in raw
    assert "Subject: Hello Bcc: evil@example.com" in raw
