"""A person's due dates and milestones in their calendar app.

Two ways, showing the same events:
- A private subscription (ICS) URL that Outlook, Google Calendar or Apple Calendar polls. Its token is derived
  from a secret and the feed's creation time, so the URL can be shown again; making a new one revokes the old.
- Outlook calendar sync: Project Hub writes the events into a "Project Hub" calendar it creates in the person's
  mailbox through Microsoft Graph, and keeps them up to date (one way: changes made in Outlook aren't read back).

What is shown: tasks assigned to the person that aren't done, due from 30 days ago to 180 days ahead, and
milestones in projects they can see, across all their accounts. Everything is worked out as that person, at the
time of the request or sync.
"""

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.access_grant import ProjectModule
from app.models.account_member import AccountMember
from app.models.integration import CalendarFeed, CalendarSyncEvent, ConnectionStatus, MicrosoftCalendarConnection
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.services.access import access_for
from app.services.email_delivery import app_base
from app.services.integrations import microsoft
from app.services.integrations.http import Http, requests_http
from app.services.integrations.secrets import IntegrationNotConfigured, TokenCipher, require_secret
from app.services.project_overview import MILESTONE_KEYS, TASK_COMPLETED_KEYS
from app.services.raid import normalize_option_key
from app.services.tasks import TaskService

logger = logging.getLogger(__name__)

PAST_DAYS = 30
AHEAD_DAYS = 180
MAX_EVENTS = 500
CALENDAR_NAME = "Project Hub"
# Graph calls per person per sync run, so one big calendar can't hold up everyone else.
MAX_CHANGES_PER_SYNC = 150


@dataclass(frozen=True)
class CalendarItem:
    key: str  # "task:<id>"; also the ICS UID
    title: str
    day: date
    path: str
    detail: str

    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.title}|{self.day}|{self.path}|{self.detail}".encode()).hexdigest()


def option_keys(option: OptionValue | None) -> set[str]:
    return {normalize_option_key(option.value), normalize_option_key(option.label)} if option else set()


def calendar_items(db: Session, user: User, *, today: date | None = None) -> list[CalendarItem]:
    today = today or date.today()
    start, end = today - timedelta(days=PAST_DAYS), today + timedelta(days=AHEAD_DAYS)
    items: dict[str, CalendarItem] = {}
    account_ids = db.scalars(select(AccountMember.account_id).where(AccountMember.user_id == user.id)).all()
    for account_id in account_ids:
        # The person's own tasks, exactly as My work finds them.
        due = TaskService(db).list_due_tasks(account_id=account_id, current_user=user, mode="BOTH", days=AHEAD_DAYS, assigned_to=user.id)
        for task in due["tasks"]:  # type: ignore[index]
            day = task.get("finish_date")
            if not day or day < start:
                continue
            key = f"task:{task['id']}"
            items[key] = CalendarItem(
                key=key,
                title=f"{task['name']} ({task['project']['name']})",
                day=day,
                path=f"/projects/{task['project_id']}/sheet?task={task['id']}",
                detail=f"Due in {task['project']['name']}",
            )
        # Milestones in every project the person can see tasks in.
        visible = access_for(db, account_id=account_id, user=user).visible_project_ids(ProjectModule.TASKS)
        rows = db.execute(
            select(Task, Project.name, OptionValue)
            .join(Project, Project.id == Task.project_id)
            .join(OptionValue, OptionValue.id == Task.task_type_id)
            .where(
                Task.account_id == account_id,
                Task.is_deleted.is_(False),
                Task.finish_date >= start,
                Task.finish_date <= end,
            )
        ).all()
        status_ids = {task.status_id for task, _, _ in rows if task.status_id}
        statuses = {o.id: o for o in db.scalars(select(OptionValue).where(OptionValue.id.in_(status_ids))).all()} if status_ids else {}
        for task, project_name, task_type in rows:
            if not (option_keys(task_type) & MILESTONE_KEYS) or (visible is not None and task.project_id not in visible):
                continue
            if option_keys(statuses.get(task.status_id)) & TASK_COMPLETED_KEYS:
                continue
            key = f"task:{task.id}"
            items[key] = CalendarItem(
                key=key,
                title=f"◆ {task.name} ({project_name})",
                day=task.finish_date,
                path=f"/projects/{task.project_id}/sheet?task={task.id}",
                detail=f"Milestone in {project_name}",
            )
    return sorted(items.values(), key=lambda item: (item.day, item.title))[:MAX_EVENTS]


# Subscription feed ---------------------------------------------------------------------------------------


def _feed_token(settings: Settings, feed: CalendarFeed) -> str:
    secret = require_secret(settings, "integration_state_secret").encode()
    created = feed.created_at if feed.created_at.tzinfo else feed.created_at.replace(tzinfo=UTC)
    digest = hmac.new(secret, f"calendar-feed:{feed.user_id}:{created.timestamp()}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def ics_fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    encoded = line.encode()
    if len(encoded) <= 75:
        return line
    parts, current = [], b""
    for char in line:
        piece = char.encode()
        if len(current) + len(piece) > (75 if not parts else 74):
            parts.append(current.decode())
            current = b""
        current += piece
    parts.append(current.decode())
    return "\r\n ".join(parts)


def render_ics(settings: Settings, items: list[CalendarItem], *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Project Hub//Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for item in items:
        url = f"{app_base(settings)}{item.path}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{item.key.replace(':', '-')}@projecthub",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{item.day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(item.day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(item.title)}",
            f"DESCRIPTION:{ics_escape(item.detail + chr(10) + url)}",
            f"URL:{url}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(line) for line in lines) + "\r\n"


class CalendarFeedService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def url(self, feed: CalendarFeed) -> str:
        return f"{app_base(self.settings)}/api/v1/calendar/{_feed_token(self.settings, feed)}.ics"

    def read(self, user: User) -> dict[str, Any]:
        feed = self.db.get(CalendarFeed, user.id)
        return {"enabled": feed is not None, "url": self.url(feed) if feed else None, "created_at": feed.created_at if feed else None}

    def create(self, user: User) -> dict[str, Any]:
        """A new URL; any earlier one stops working."""
        feed = self.db.get(CalendarFeed, user.id)
        if feed is not None:
            self.db.delete(feed)
            self.db.flush()
        try:
            feed = CalendarFeed(user_id=user.id, created_at=datetime.now(UTC), token_hash="")
            feed.token_hash = _hash(_feed_token(self.settings, feed))
        except IntegrationNotConfigured as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Calendar feeds aren't set up yet.") from exc
        self.db.add(feed)
        self.db.commit()
        return self.read(user)

    def delete(self, user: User) -> None:
        feed = self.db.get(CalendarFeed, user.id)
        if feed is not None:
            self.db.delete(feed)
            self.db.commit()

    def render(self, token: str) -> str:
        feed = self.db.scalar(select(CalendarFeed).where(CalendarFeed.token_hash == _hash(token)))
        user = self.db.get(User, feed.user_id) if feed else None
        if feed is None or user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar not found.")
        feed.last_fetched_at = datetime.now(UTC)
        body = render_ics(self.settings, calendar_items(self.db, user))
        self.db.commit()
        return body


# Outlook sync --------------------------------------------------------------------------------------------


def graph_event(settings: Settings, item: CalendarItem) -> dict[str, Any]:
    return {
        "subject": item.title[:255],
        "body": {"contentType": "text", "content": f"{item.detail}\n{app_base(settings)}{item.path}"},
        "start": {"dateTime": f"{item.day.isoformat()}T00:00:00", "timeZone": "UTC"},
        "end": {"dateTime": f"{(item.day + timedelta(days=1)).isoformat()}T00:00:00", "timeZone": "UTC"},
        "isAllDay": True,
        "showAs": "free",
        "isReminderOn": False,
    }


class CalendarSyncService:
    def __init__(self, db: Session, settings: Settings, *, http: Http = requests_http) -> None:
        self.db = db
        self.settings = settings
        self.http = http

    def read(self, user: User) -> dict[str, Any] | None:
        connection = self.db.get(MicrosoftCalendarConnection, user.id)
        if connection is None:
            return None
        return {
            "email": connection.email,
            "status": connection.status,
            "last_error": connection.last_error,
            "last_synced_at": connection.last_synced_at,
        }

    def connect(self, user: User, *, me_id: str, email: str | None, tokens: microsoft.Tokens) -> None:
        connection = self.db.get(MicrosoftCalendarConnection, user.id)
        if connection is not None and connection.microsoft_user_id != me_id:
            # Another mailbox: forget the events written to the old one.
            self.db.query(CalendarSyncEvent).filter_by(user_id=user.id).delete()
            self.db.delete(connection)
            self.db.flush()
            connection = None
        if connection is None:
            connection = MicrosoftCalendarConnection(user_id=user.id, microsoft_user_id=me_id, encrypted_token=b"")
            self.db.add(connection)
        connection.email = email
        connection.encrypted_token = TokenCipher(self.settings).encrypt(user.id, tokens.refresh_token)
        connection.status = ConnectionStatus.ACTIVE.value
        connection.last_error = None
        self.db.commit()
        self.sync(user.id, access_token=tokens.access_token)

    def disconnect(self, user: User) -> None:
        """Stop syncing and remove the Project Hub calendar from Outlook (best effort)."""
        connection = self.db.get(MicrosoftCalendarConnection, user.id)
        if connection is None:
            return
        try:
            client = self.client(connection)
            if connection.calendar_id:
                client.request("DELETE", f"/me/calendars/{connection.calendar_id}")
        except (microsoft.GraphError, IntegrationNotConfigured, ValueError):
            pass
        self.db.query(CalendarSyncEvent).filter_by(user_id=user.id).delete()
        self.db.delete(connection)
        self.db.commit()

    def client(self, connection: MicrosoftCalendarConnection) -> microsoft.GraphClient:
        cipher = TokenCipher(self.settings)
        tokens = microsoft.refresh(self.settings, self.http, "calendar", cipher.decrypt(connection.user_id, connection.encrypted_token))
        connection.encrypted_token = cipher.encrypt(connection.user_id, tokens.refresh_token)
        self.db.commit()
        return microsoft.GraphClient(tokens.access_token, self.http)

    def sync_all(self) -> dict[str, int]:
        counts = {"synced": 0, "failed": 0}
        user_ids = self.db.scalars(
            select(MicrosoftCalendarConnection.user_id).where(MicrosoftCalendarConnection.status == ConnectionStatus.ACTIVE.value)
        ).all()
        for user_id in user_ids:
            counts["synced" if self.sync(user_id) else "failed"] += 1
        return counts

    def sync(self, user_id: UUID, *, access_token: str | None = None) -> bool:
        connection = self.db.get(MicrosoftCalendarConnection, user_id)
        user = self.db.get(User, user_id)
        if connection is None or user is None or not user.is_active:
            return False
        try:
            client = microsoft.GraphClient(access_token, self.http) if access_token else self.client(connection)
            if not connection.calendar_id or not client.calendar_exists(connection.calendar_id):
                connection.calendar_id = client.create_calendar(CALENDAR_NAME)
                self.db.query(CalendarSyncEvent).filter_by(user_id=user_id).delete()
                self.db.commit()
            self.apply(client, connection, calendar_items(self.db, user))
        except microsoft.GraphError as error:
            self.db.rollback()
            connection = self.db.get(MicrosoftCalendarConnection, user_id)  # type: ignore[assignment]
            assert connection is not None
            connection.last_error = str(error)[:500]
            if error.needs_reconnect:
                connection.status = ConnectionStatus.ERROR.value
            self.db.commit()
            logger.warning("Outlook calendar sync failed", extra={"user_id": str(user_id), "status": error.status})
            return False
        connection.last_synced_at = datetime.now(UTC)
        connection.last_error = None
        self.db.commit()
        return True

    def apply(self, client: microsoft.GraphClient, connection: MicrosoftCalendarConnection, items: list[CalendarItem]) -> None:
        existing = {row.source_key: row for row in self.db.scalars(select(CalendarSyncEvent).where(CalendarSyncEvent.user_id == connection.user_id)).all()}
        wanted = {item.key: item for item in items}
        changes = 0
        for key, row in existing.items():
            if key not in wanted and changes < MAX_CHANGES_PER_SYNC:
                client.delete_event(row.event_id)
                self.db.delete(row)
                changes += 1
        for key, item in wanted.items():
            if changes >= MAX_CHANGES_PER_SYNC:
                break
            row = existing.get(key)
            digest = item.content_hash()
            if row is None:
                event_id = client.create_event(connection.calendar_id or "", graph_event(self.settings, item))
                self.db.add(CalendarSyncEvent(user_id=connection.user_id, source_key=key, event_id=event_id, content_hash=digest))
                changes += 1
            elif row.content_hash != digest:
                try:
                    client.update_event(row.event_id, graph_event(self.settings, item))
                except microsoft.GraphError as error:
                    if error.status != 404:
                        raise
                    # Deleted in Outlook: write it again.
                    row.event_id = client.create_event(connection.calendar_id or "", graph_event(self.settings, item))
                row.content_hash = digest
                changes += 1
            else:
                continue
            # Record each write as it happens, so a failure part-way never leaves events we don't know about.
            self.db.commit()
        self.db.commit()
