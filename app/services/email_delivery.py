"""Email delivery for notifications, run by the scheduled worker in app/worker.py.

Requests only mark a notification's email as PENDING or DIGEST (NotificationService.create_notification).
Sending happens here, outside the request, so a slow or failing SES call never slows down or breaks the app.
Everything is re-checked at send time: the person's settings, their access to the item, and suppressions.
"""

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.access_grant import ProjectModule
from app.models.account import Account
from app.models.account_member import AccountMember
from app.models.comment import Comment
from app.models.decision_option import DecisionOption
from app.models.email_suppression import EmailSuppression
from app.models.notification import EmailStatus, Notification, NotificationType
from app.models.notification_preference import EmailFrequency
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.support_ticket import SupportTicket
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.tasks import TaskRepository
from app.services.access import COMMENTABLE_MODELS, access_for, can_see_entity
from app.services.email_sender import EmailSender, NoEmailRoute, OutgoingEmail
from app.services.notification_preferences import (
    CATEGORY_LABELS,
    MY_WORK_SUMMARY,
    UNSUBSCRIBE_CATEGORIES,
    NotificationPreferenceService,
    category_for,
    zone_for,
)
from app.services.notifications import NotificationService

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 5
# A run that dies mid-batch leaves rows SENDING; another run picks them up after this long.
CLAIM_TIMEOUT = timedelta(minutes=10)
# Don't remind someone about the same task twice in a row.
DUE_SOON_REPEAT_AFTER = timedelta(days=3)
DIGEST_ITEM_LIMIT = 50
# The Home summary lists at most this many tasks or mentions per section.
SUMMARY_SECTION_LIMIT = 15
# How far ahead the summary looks for tasks due soon.
MY_WORK_HORIZON_DAYS = 60
# Unread mentions from the last day count as new.
NEW_MENTION_WINDOW = timedelta(days=1)
SETTINGS_PATH = "/Account?tab=notifications"
MY_WORK_PATH = "/"

# Records without their own detail page open on the screen that lists them, like the notification bell.
LIST_PAGES = {
    "RISK": "/RiskLog",
    "ISSUE": "/IssueLog",
    "ASSUMPTION": "/AssumptionLog",
    "DECISION": "/DecisionLog",
    "CHANGE_REQUEST": "/ChangeRegister",
    "REQUIREMENT": "/Requirements",
    "TEST_CASE": "/Testing",
    "SPRINT": "/SprintManagement",
}
KIND_LABELS = {
    "PORTFOLIO": "Portfolio",
    "PROGRAM": "Program",
    "PROJECT": "Project",
    "TASK": "Task",
    "SPRINT": "Sprint",
    "RISK": "Risk",
    "ISSUE": "Issue",
    "ASSUMPTION": "Assumption",
    "DECISION": "Decision",
    "DEPENDENCY": "Dependency",
    "CHANGE_REQUEST": "Change request",
    "REQUIREMENT": "Requirement",
    "TEST_CASE": "Test case",
    "SUPPORT_TICKET": "Support ticket",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- Unsubscribe links -------------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_unsubscribe_token(secret: str, user_id: UUID, category: str) -> str:
    body = _b64(f"{user_id}:{category}".encode())
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def read_unsubscribe_token(secret: str, token: str) -> tuple[UUID, str] | None:
    """(user_id, category) from a link we signed, or None. The category may be "all"."""
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not secret or not hmac.compare_digest(_unb64(signature), expected):
            return None
        user_id, category = _unb64(body).decode().split(":", 1)
        if category != "all" and category not in UNSUBSCRIBE_CATEGORIES:
            return None
        return UUID(user_id), category
    except (ValueError, UnicodeDecodeError):
        return None


def unsubscribe_url(settings: Settings, user_id: UUID, category: str) -> str:
    token = make_unsubscribe_token(settings.email_token_secret, user_id, category)
    return f"{app_base(settings)}/api/v1/email/unsubscribe?token={quote(token)}"


def app_base(settings: Settings) -> str:
    return settings.app_url.rstrip("/")


# --- What a notification points at -------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemRef:
    kind: str
    label: str
    path: str


def _truncate(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_item(db: Session, entity_type: str, entity_id: UUID) -> ItemRef | None:
    """The record a notification is about, with the app path that opens it."""
    entity_type = entity_type.upper()
    if entity_type == "COMMENT":
        comment = db.get(Comment, entity_id)
        return describe_item(db, comment.entity_type, comment.entity_id) if comment else None
    if entity_type == "DECISION_OPTION":
        option = db.get(DecisionOption, entity_id)
        return describe_item(db, "DECISION", option.decision_id) if option else None
    if entity_type == "SUPPORT_TICKET":
        ticket = db.get(SupportTicket, entity_id)
        if ticket is None:
            return None
        label = f"{ticket.ticket_number} · {_truncate(ticket.subject or ticket.description)}"
        return ItemRef(kind="Support ticket", label=label, path=f"/LearnSupport?ticket={ticket.id}")
    model = COMMENTABLE_MODELS.get(entity_type)
    record: Any = db.get(model, entity_id) if model is not None else None
    if record is None:
        return None

    label = getattr(record, "title", None) or getattr(record, "name", None) or getattr(record, "description", None)
    number = getattr(record, f"{entity_type.lower()}_number", None)
    label = _truncate(label or KIND_LABELS.get(entity_type, "Item"))
    if number:
        label = f"{number} · {label}"

    if entity_type == "TASK":
        # Opens the task's detail panel on the project sheet (QW-1).
        path = f"/projects/{record.project_id}/sheet?task={record.id}"
    elif entity_type == "PROJECT":
        path = f"/projects/{record.id}/sheet"
    elif entity_type == "PROGRAM":
        path = f"/programs/{record.id}"
    elif entity_type == "PORTFOLIO":
        path = f"/portfolios/{record.id}"
    elif entity_type == "DEPENDENCY":
        path = f"/projects/{record.project_id}/sheet"
    else:
        path = LIST_PAGES.get(entity_type, "/")
    return ItemRef(kind=KIND_LABELS.get(entity_type, "Item"), label=label, path=path)


# --- Rendering ---------------------------------------------------------------------------------------------

NAVY = "#002046"
ORANGE = "#c87b14"


def _html_page(*, preheader: str, content: str, footer: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1e293b">
<span style="display:none;max-height:0;overflow:hidden">{escape(preheader)}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0">
<tr><td style="background:{NAVY};border-radius:12px 12px 0 0;padding:14px 24px;color:#ffffff;font-size:14px;font-weight:600">Project Hub</td></tr>
<tr><td style="padding:24px">{content}</td></tr>
</table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px">
<tr><td style="padding:16px 24px;font-size:12px;line-height:18px;color:#64748b">{footer}</td></tr>
</table>
</td></tr></table>
</body></html>"""


def _button(url: str, text: str, *, color: str = ORANGE) -> str:
    return (
        f'<a href="{escape(url)}" style="display:inline-block;background:{color};color:#ffffff;text-decoration:none;'
        f'font-weight:600;font-size:14px;padding:10px 18px;border-radius:8px">{escape(text)}</a>'
    )


def _link(url: str, text: str) -> str:
    return f'<a href="{escape(url)}" style="color:#475569">{escape(text)}</a>'


def render_notification_email(
    *,
    settings: Settings,
    user: User,
    notification: Notification,
    item: ItemRef | None,
    account_name: str,
) -> OutgoingEmail:
    category = category_for(notification.notification_type) or "all"
    category_label = CATEGORY_LABELS.get(category, "notifications")
    base = app_base(settings)
    item_url = f"{base}{item.path if item else '/'}"
    settings_url = f"{base}{SETTINGS_PATH}"
    unsubscribe = unsubscribe_url(settings, user.id, category)
    subject = f"{notification.title}: {item.label}" if item else notification.title

    text_lines = [notification.title, ""]
    if notification.message:
        text_lines += [notification.message, ""]
    if item:
        text_lines += [f"{item.kind}: {item.label}"]
    text_lines += [
        f"Open in Project Hub: {item_url}",
        "",
        "--",
        f"{account_name} · Project Hub",
        f"You get these emails for {category_label}. Email settings: {settings_url}",
        f"Unsubscribe from {category_label} emails: {unsubscribe}",
    ]

    item_html = (
        f'<p style="margin:16px 0 0;padding:12px 14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;'
        f'font-size:14px"><span style="color:#64748b">{escape(item.kind)}</span><br>'
        f'<strong style="color:{NAVY}">{escape(item.label)}</strong></p>'
        if item
        else ""
    )
    content = (
        f'<p style="margin:0 0 4px;font-size:12px;color:#64748b">{escape(account_name)}</p>'
        f'<h1 style="margin:0;font-size:20px;line-height:28px;color:{NAVY}">{escape(notification.title)}</h1>'
        + (
            f'<p style="margin:8px 0 0;font-size:14px;line-height:22px">{escape(notification.message)}</p>'
            if notification.message
            else ""
        )
        + item_html
        + f'<p style="margin:20px 0 0">{_button(item_url, "Open in Project Hub")}</p>'
    )
    footer = (
        f"You get these emails for {escape(category_label)}. "
        f"{_link(settings_url, 'Email settings')} · {_link(unsubscribe, f'Unsubscribe from {category_label} emails')}"
    )
    return OutgoingEmail(
        to=user.email,
        subject=_truncate(subject, 150),
        text="\n".join(text_lines),
        html=_html_page(preheader=notification.message or notification.title, content=content, footer=footer),
        headers={"List-Unsubscribe": f"<{unsubscribe}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    )


# The RAID tab each escalated item opens on; risks and issues also open their detail panel (?id=).
ESCALATION_TABS = {"RISK": "risks", "ISSUE": "issues", "ASSUMPTION": "assumptions", "DEPENDENCY": "dependencies"}


def escalation_path(entity_type: str, entity_id: UUID) -> str:
    tab = ESCALATION_TABS.get(entity_type.upper(), "risks")
    return f"/RiskLog?tab={tab}&id={entity_id}"


def render_escalation_email(
    *, settings: Settings, user: User, notification: Notification, account_name: str
) -> OutgoingEmail:
    """"An escalation needs your attention": who escalated what to whom, the item's key facts and the note."""
    details = notification.details or {}
    actor = details.get("actor") or "Someone"
    base = app_base(settings)
    item_url = f"{base}{escalation_path(notification.entity_type, notification.entity_id)}"
    settings_url = f"{base}{SETTINGS_PATH}"
    heading = "An escalation needs your attention"
    intro = f"{notification.title}. Please review the details below."
    rows = [
        ("Reference", details.get("reference")),
        ("Title", details.get("title")),
        ("Project", details.get("project")),
        ("Priority", details.get("priority")),
        ("Escalated to", details.get("escalated_to")),
    ]
    rows = [(label, str(value)) for label, value in rows if value]
    reference = details.get("reference") or KIND_LABELS.get(notification.entity_type, "Item")
    subject = f"Escalation: {reference} · {details.get('title') or notification.title}"

    text_lines = [heading, "", intro, ""]
    text_lines += [f"{label}: {value}" for label, value in rows]
    if notification.message:
        text_lines += ["", f"Note from {actor}:", notification.message]
    text_lines += [
        "",
        f"Open in Project Hub: {item_url}",
        "",
        "--",
        f"{account_name} · Project Hub",
        f"You got this email because {actor} escalated this to you. Email settings: {settings_url}",
    ]

    table = "".join(
        f'<tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;width:130px;font-size:14px;color:#64748b">'
        f"{escape(label)}</td>"
        f'<td style="padding:10px 0;border-top:1px solid #e2e8f0;font-size:14px;color:#0f172a;'
        f'{"font-weight:700;color:" + NAVY if label == "Reference" else ""}">{escape(value)}</td></tr>'
        for label, value in rows
    )
    note_html = (
        f'<p style="margin:20px 0 4px;font-size:12px;color:#64748b">Note from {escape(actor)}</p>'
        f'<p style="margin:0;font-size:14px;line-height:22px;white-space:pre-line">{escape(notification.message)}</p>'
        if notification.message
        else ""
    )
    content = (
        f'<p style="margin:0 0 4px;font-size:12px;color:#64748b">{escape(account_name)}</p>'
        f'<h1 style="margin:0;font-size:20px;line-height:28px;color:{NAVY}">{escape(heading)}</h1>'
        f'<p style="margin:8px 0 0;font-size:14px;line-height:22px;color:#475569">{escape(intro)}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin-top:16px;border-bottom:1px solid #e2e8f0">{table}</table>'
        + note_html
        + f'<p style="margin:24px 0 0">{_button(item_url, "Open in Project Hub", color=NAVY)}</p>'
    )
    footer = f"You got this email because {escape(actor)} escalated this to you. {_link(settings_url, 'Email settings')}"
    return OutgoingEmail(
        to=user.email,
        subject=_truncate(subject, 150),
        text="\n".join(text_lines),
        html=_html_page(preheader=intro, content=content, footer=footer),
    )


@dataclass(frozen=True)
class DigestEntry:
    notification: Notification
    item: ItemRef | None
    account_name: str


@dataclass(frozen=True)
class SummaryTask:
    task_id: UUID
    name: str
    project_name: str
    path: str
    days: int  # from today; negative when overdue
    blocked: bool
    account_name: str


@dataclass
class MyWorkSummary:
    """The Home summary: overdue, due-today and blocked work and new mentions this morning, across every account (IN-2)."""

    overdue: list[SummaryTask] = field(default_factory=list)
    due_today: list[SummaryTask] = field(default_factory=list)
    # Blocked tasks that aren't already listed as overdue or due today.
    blocked: list[SummaryTask] = field(default_factory=list)
    mentions: list[DigestEntry] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.overdue or self.due_today or self.blocked or self.mentions)

    def task_ids(self) -> set[UUID]:
        return {task.task_id for task in self.overdue + self.due_today + self.blocked}

    def account_names(self) -> set[str]:
        tasks = self.overdue + self.due_today + self.blocked
        return {task.account_name for task in tasks} | {entry.account_name for entry in self.mentions}


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def digest_subject(summary: MyWorkSummary | None, update_count: int) -> str:
    parts: list[str] = []
    if summary is not None:
        if summary.overdue:
            parts.append(f"{len(summary.overdue)} overdue")
        if summary.due_today:
            parts.append(f"{len(summary.due_today)} due today")
        if summary.blocked:
            parts.append(f"{len(summary.blocked)} blocked")
        if summary.mentions:
            parts.append(_plural(len(summary.mentions), "new mention"))
    if update_count:
        parts.append(_plural(update_count, "update"))
    return "Your Project Hub digest: " + ", ".join(parts)


def _when(task: SummaryTask) -> str:
    if task.days < 0:
        return f"{_plural(-task.days, 'day')} overdue"
    if task.days == 0:
        return "Due today"
    if task.days == 1:
        return "Due tomorrow"
    return f"Due in {task.days} days"


def _section_heading(title: str, count: int) -> str:
    return (
        f'<p style="margin:24px 0 0;padding-bottom:6px;border-bottom:1px solid #e2e8f0;font-size:12px;font-weight:600;'
        f'color:#64748b;text-transform:uppercase;letter-spacing:.04em">{escape(title)} ({count})</p>'
    )


def _html_line(url: str, heading: str, detail: str) -> str:
    return (
        '<p style="margin:12px 0 0;font-size:14px;line-height:20px">'
        f'<a href="{escape(url)}" style="color:{NAVY};font-weight:600;text-decoration:none">{escape(heading)}</a>'
        + (f'<br><span style="color:#475569">{escape(detail)}</span>' if detail else "")
        + "</p>"
    )


def _more(total: int, url: str, where: str, text_lines: list[str], html_parts: list[str]) -> None:
    hidden = total - SUMMARY_SECTION_LIMIT
    if hidden > 0:
        text_lines += [f"…and {hidden} more {where}: {url}", ""]
        html_parts.append(
            f'<p style="margin:12px 0 0;font-size:14px;color:#475569">…and {hidden} more '
            f'<a href="{escape(url)}" style="color:#475569">{escape(where)}</a>.</p>'
        )


def _render_summary(summary: MyWorkSummary, base: str, text_lines: list[str], html_parts: list[str]) -> None:
    show_account = len(summary.account_names()) > 1
    for title, tasks in (("Overdue", summary.overdue), ("Due today", summary.due_today), ("Blocked", summary.blocked)):
        if not tasks:
            continue
        text_lines += [f"{title.upper()} ({len(tasks)})", ""]
        html_parts.append(_section_heading(title, len(tasks)))
        for task in tasks[:SUMMARY_SECTION_LIMIT]:
            url = f"{base}{task.path}"
            details = [task.project_name]
            if title != "Due today":
                details.append(_when(task))
            if task.blocked and title != "Blocked":
                details.append("Blocked")
            if show_account:
                details.append(task.account_name)
            detail = " · ".join(details)
            text_lines += [f"- {task.name}", f"  {detail}", f"  {url}", ""]
            html_parts.append(_html_line(url, task.name, detail))
        _more(len(tasks), base + MY_WORK_PATH, "on Home", text_lines, html_parts)

    if summary.mentions:
        mentions = summary.mentions
        text_lines += [f"NEW MENTIONS ({len(mentions)})", ""]
        html_parts.append(_section_heading("New mentions", len(mentions)))
        for entry in mentions[:SUMMARY_SECTION_LIMIT]:
            url = f"{base}{entry.item.path if entry.item else '/'}"
            heading = entry.item.label if entry.item else entry.notification.title
            details = [entry.notification.message or entry.notification.title]
            if show_account:
                details.append(entry.account_name)
            detail = " · ".join(details)
            text_lines += [f"- {heading}", f"  {detail}", f"  {url}", ""]
            html_parts.append(_html_line(url, heading, detail))
        _more(len(mentions), base + "/", "under the bell in Project Hub", text_lines, html_parts)


def render_digest_email(
    *,
    settings: Settings,
    user: User,
    entries: list[DigestEntry],
    summary: MyWorkSummary | None = None,
) -> OutgoingEmail:
    """The daily email: the Home summary (when on and not empty), then notifications queued for the digest."""
    if summary is not None and summary.is_empty():
        summary = None
    base = app_base(settings)
    settings_url = f"{base}{SETTINGS_PATH}"
    unsubscribe = unsubscribe_url(settings, user.id, "all")
    shown = entries[:DIGEST_ITEM_LIMIT]
    hidden = len(entries) - len(shown)
    subject = digest_subject(summary, len(entries))

    text_lines = [subject, ""]
    html_parts = [f'<h1 style="margin:0 0 4px;font-size:20px;line-height:28px;color:{NAVY}">{escape(subject)}</h1>']
    if summary is not None:
        _render_summary(summary, base, text_lines, html_parts)
        if entries:
            text_lines += [f"OTHER UPDATES ({len(entries)})", ""]
            html_parts.append(_section_heading("Other updates", len(entries)))

    by_account: dict[str, list[DigestEntry]] = {}
    for entry in shown:
        by_account.setdefault(entry.account_name, []).append(entry)
    show_account = len(by_account) > 1
    for account_name, account_entries in by_account.items():
        if show_account:
            text_lines += [account_name.upper(), ""]
            html_parts.append(
                f'<p style="margin:20px 0 0;font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase">'
                f"{escape(account_name)}</p>"
            )
        for entry in account_entries:
            url = f"{base}{entry.item.path if entry.item else '/'}"
            heading = entry.notification.title + (f": {entry.item.label}" if entry.item else "")
            text_lines += [f"- {heading}"]
            if entry.notification.message:
                text_lines += [f"  {entry.notification.message}"]
            text_lines += [f"  {url}", ""]
            html_parts.append(_html_line(url, heading, entry.notification.message or ""))
    if hidden:
        text_lines += [f"…and {hidden} more in Project Hub: {base}/", ""]
        html_parts.append(f'<p style="margin:14px 0 0;font-size:14px;color:#475569">…and {hidden} more.</p>')

    if summary is not None:
        button = _button(base + MY_WORK_PATH, "Open Home")
        why = "You get this daily digest for your Home summary and the updates you chose."
    else:
        button = _button(base + "/", "Open Project Hub")
        why = "You get this daily digest for the updates you chose."
    html_parts.append(f'<p style="margin:24px 0 0">{button}</p>')

    text_lines += ["--", f"{why} Email settings: {settings_url}"]
    footer_links = [_link(settings_url, "Email settings")]
    if summary is not None:
        stop_summary = unsubscribe_url(settings, user.id, MY_WORK_SUMMARY)
        text_lines += [f"Stop the Home summary: {stop_summary}"]
        footer_links.append(_link(stop_summary, "Stop the Home summary"))
    text_lines += [f"Unsubscribe from all Project Hub emails: {unsubscribe}"]
    footer_links.append(_link(unsubscribe, "Unsubscribe from all Project Hub emails"))
    return OutgoingEmail(
        to=user.email,
        subject=subject,
        text="\n".join(text_lines),
        html=_html_page(preheader=subject, content="".join(html_parts), footer=f"{why} " + " · ".join(footer_links)),
        headers={"List-Unsubscribe": f"<{unsubscribe}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    )


# --- Delivery ----------------------------------------------------------------------------------------------


class EmailDeliveryService:
    def __init__(self, db: Session, settings: Settings, sender: EmailSender | None) -> None:
        self.db = db
        self.settings = settings
        self.sender = sender
        self.preferences = NotificationPreferenceService(db)
        self.account_members = AccountMemberRepository(db)
        self._account_names: dict[UUID, str] = {}

    # Right-away emails (every minute) ----------------------------------------------------------------------

    def deliver_pending(self, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
        now = now or utcnow()
        counts = {"sent": 0, "skipped": 0, "digest": 0, "failed": 0, "retry": 0}
        for notification_id in self.claim_pending(now=now, limit=limit):
            notification = self.db.get(Notification, notification_id)
            if notification is not None:
                counts[self.deliver_one(notification, now=now)] += 1
        return counts

    def claim_pending(self, *, now: datetime, limit: int) -> list[UUID]:
        """Mark a batch SENDING so an overlapping run leaves it alone."""
        statement = (
            select(Notification.id)
            .where(
                or_(
                    Notification.email_status == EmailStatus.PENDING.value,
                    and_(
                        Notification.email_status == EmailStatus.SENDING.value,
                        Notification.email_claimed_at < now - CLAIM_TIMEOUT,
                    ),
                )
            )
            .order_by(Notification.created_at, Notification.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        ids = list(self.db.scalars(statement).all())
        if ids:
            self.db.execute(
                update(Notification)
                .where(Notification.id.in_(ids))
                .values(email_status=EmailStatus.SENDING.value, email_claimed_at=now)
            )
        self.db.commit()
        return ids

    def deliver_one(self, notification: Notification, *, now: datetime) -> str:
        user = self.db.get(User, notification.user_id)
        if user is None or not self.can_email(user, notification):
            return self.finish(notification, EmailStatus.SKIPPED)
        # Settings may have changed since the notification was queued.
        frequency = self.preferences.frequency_for(user.id, notification.notification_type)
        if frequency == EmailFrequency.OFF:
            return self.finish(notification, EmailStatus.SKIPPED)
        if frequency == EmailFrequency.DIGEST:
            notification.email_claimed_at = None
            self.finish(notification, EmailStatus.DIGEST)
            return "digest"
        if self.sender is None:
            notification.email_status = EmailStatus.PENDING.value
            self.db.commit()
            return "retry"

        if notification.notification_type == NotificationType.ESCALATION.value:
            rendered = render_escalation_email(
                settings=self.settings,
                user=user,
                notification=notification,
                account_name=self.account_name(notification.account_id),
            )
        else:
            rendered = render_notification_email(
                settings=self.settings,
                user=user,
                notification=notification,
                item=describe_item(self.db, notification.entity_type, notification.entity_id),
                account_name=self.account_name(notification.account_id),
            )
        email = replace(rendered, account_id=notification.account_id)
        try:
            self.sender.send(email)
        except NoEmailRoute:
            # Neither SES nor the account's Microsoft 365 mailbox can send it any more.
            return self.finish(notification, EmailStatus.SKIPPED)
        except Exception:
            notification.email_attempts += 1
            notification.email_claimed_at = None
            failed = notification.email_attempts >= MAX_SEND_ATTEMPTS
            logger.exception(
                "Notification email failed",
                extra={"notification_id": str(notification.id), "attempts": notification.email_attempts},
            )
            self.finish(notification, EmailStatus.FAILED if failed else EmailStatus.PENDING)
            return "failed" if failed else "retry"
        notification.emailed_at = now
        self.finish(notification, EmailStatus.SENT)
        return "sent"

    def finish(self, notification: Notification, email_status: EmailStatus) -> str:
        notification.email_status = email_status.value
        self.db.add(notification)
        self.db.commit()
        return email_status.value.lower()

    def can_email_user(self, user: User) -> bool:
        return bool(user.is_active and user.email and not self.preferences.is_suppressed(user.email))

    def can_email(self, user: User, notification: Notification) -> bool:
        if not self.can_email_user(user):
            return False
        if self.account_members.get_for_user(account_id=notification.account_id, user_id=user.id) is None:
            return False
        # Access may have been removed since the notification was created.
        return can_see_entity(
            self.db,
            account_id=notification.account_id,
            user=user,
            entity_type=notification.entity_type,
            entity_id=notification.entity_id,
        )

    def only_account(self, user_id: UUID) -> UUID | None:
        """The person's account if they belong to just one; a digest spanning accounts is sent by SES."""
        account_ids = self.db.scalars(select(AccountMember.account_id).where(AccountMember.user_id == user_id).limit(2)).all()
        return account_ids[0] if len(account_ids) == 1 else None

    def account_name(self, account_id: UUID) -> str:
        if account_id not in self._account_names:
            account = self.db.get(Account, account_id)
            self._account_names[account_id] = account.name if account else "Project Hub"
        return self._account_names[account_id]

    # Morning run (hourly): due-soon reminders, then the digest ---------------------------------------------

    def run_morning(self, *, now: datetime | None = None) -> dict[str, int]:
        """For everyone whose local time has reached their digest hour today, once per local day."""
        now = now or utcnow()
        counts = {"due_soon": 0, "digests": 0}
        member_ids = select(AccountMember.user_id).distinct()
        users = list(self.db.scalars(select(User).where(User.is_active.is_(True), User.id.in_(member_ids))).all())
        for user in users:
            preference = self.preferences.get_or_default(user.id)
            local_now = now.astimezone(zone_for(preference))
            local_today = local_now.date()
            if local_now.hour < preference.digest_hour:
                continue
            if preference.last_due_soon_on != local_today:
                counts["due_soon"] += self.create_due_soon(user, local_today=local_today, now=now)
                preference = self.preferences.get_or_create(user.id)
                preference.last_due_soon_on = local_today
                self.db.commit()
            if preference.last_digest_on != local_today and self.sender is not None:
                sent = self.send_digest(
                    user, now=now, local_today=local_today if preference.my_work_summary else None
                )
                if sent is not None:
                    counts["digests"] += int(sent)
                    preference = self.preferences.get_or_create(user.id)
                    preference.last_digest_on = local_today
                    self.db.commit()
        return counts

    def create_due_soon(self, user: User, *, local_today: date, now: datetime) -> int:
        """In-app DUE_SOON notifications (emailed per the person's settings) for their tasks due today or tomorrow."""
        created = 0
        tasks = TaskRepository(self.db)
        notifications = NotificationService(self.db)
        account_ids = self.db.scalars(select(AccountMember.account_id).where(AccountMember.user_id == user.id)).all()
        for account_id in account_ids:
            try:
                visible = access_for(self.db, account_id=account_id, user=user).visible_project_ids(ProjectModule.TASKS)
            except HTTPException:
                continue
            # The due-tasks query (list_due_tasks), with assigned_to = this person.
            rows = tasks.list_due_tasks(
                account_id=account_id,
                today=local_today,
                horizon=local_today + timedelta(days=1),
                include_overdue=False,
                include_upcoming=True,
                assigned_to=user.id,
            )
            for task, project, _program in rows:
                if visible is not None and task.project_id not in visible:
                    continue
                if self.recently_reminded(user.id, task.id, now=now):
                    continue
                when = "today" if task.finish_date == local_today else "tomorrow"
                notification = notifications.create_notification(
                    account_id=account_id,
                    user_id=user.id,
                    entity_type="TASK",
                    entity_id=task.id,
                    notification_type=NotificationType.DUE_SOON,
                    title=f"Task due {when}",
                    message=f"{task.name} ({project.name}) is due {when}.",
                )
                created += notification is not None
        self.db.commit()
        return created

    def recently_reminded(self, user_id: UUID, task_id: UUID, *, now: datetime) -> bool:
        return (
            self.db.scalar(
                select(Notification.id)
                .where(
                    Notification.user_id == user_id,
                    Notification.entity_type == "TASK",
                    Notification.entity_id == task_id,
                    Notification.notification_type == NotificationType.DUE_SOON.value,
                    Notification.created_at >= now - DUE_SOON_REPEAT_AFTER,
                )
                .limit(1)
            )
            is not None
        )

    def send_digest(self, user: User, *, now: datetime, local_today: date | None = None) -> bool | None:
        """True: sent. False: nothing to send. None: SES failed, so try again next hour.

        With local_today, the email starts with the person's Home summary for that day.
        """
        queued = list(
            self.db.scalars(
                select(Notification)
                .where(Notification.user_id == user.id, Notification.email_status == EmailStatus.DIGEST.value)
                .order_by(Notification.created_at, Notification.id)
            ).all()
        )
        entries: list[DigestEntry] = []
        for notification in queued:
            if (
                not self.can_email(user, notification)
                or self.preferences.frequency_for(user.id, notification.notification_type) == EmailFrequency.OFF
            ):
                notification.email_status = EmailStatus.SKIPPED.value
                continue
            entries.append(
                DigestEntry(
                    notification=notification,
                    item=describe_item(self.db, notification.entity_type, notification.entity_id),
                    account_name=self.account_name(notification.account_id),
                )
            )
        self.db.commit()
        summary = None
        if local_today is not None and self.can_email_user(user):
            summary = self.my_work_summary(
                user,
                local_today=local_today,
                now=now,
                already_listed={entry.notification.id for entry in entries},
            )
        # Due-soon reminders for tasks the summary lists would say the same thing twice; they go out as part of it.
        in_summary = summary.task_ids() if summary is not None else set()
        shown = [
            entry
            for entry in entries
            if not (
                entry.notification.notification_type == NotificationType.DUE_SOON.value
                and entry.notification.entity_id in in_summary
            )
        ]
        if not shown and (summary is None or summary.is_empty()):
            return False
        assert self.sender is not None
        email = render_digest_email(settings=self.settings, user=user, entries=shown, summary=summary)
        try:
            self.sender.send(replace(email, account_id=self.only_account(user.id)))
        except NoEmailRoute:
            return False
        except Exception:
            logger.exception("Digest email failed", extra={"user_id": str(user.id)})
            return None
        for entry in entries:
            entry.notification.email_status = EmailStatus.SENT.value
            entry.notification.emailed_at = now
        self.db.commit()
        return True

    def my_work_summary(
        self, user: User, *, local_today: date, now: datetime, already_listed: set[UUID]
    ) -> MyWorkSummary:
        """Overdue, due today and blocked tasks assigned to the person, plus unread mentions from the last day.

        Uses the due-tasks query (list_due_tasks). Mentions already queued for this digest
        (already_listed) are left to the updates section.
        """
        summary = MyWorkSummary()
        tasks = TaskRepository(self.db)
        account_ids = self.db.scalars(
            select(AccountMember.account_id).where(AccountMember.user_id == user.id).order_by(AccountMember.created_at)
        ).all()
        for account_id in account_ids:
            try:
                visible = access_for(self.db, account_id=account_id, user=user).visible_project_ids(ProjectModule.TASKS)
            except HTTPException:
                continue
            rows = tasks.list_due_tasks(
                account_id=account_id,
                today=local_today,
                horizon=local_today + timedelta(days=MY_WORK_HORIZON_DAYS),
                include_overdue=True,
                include_upcoming=True,
                assigned_to=user.id,
            )
            blocked_ids = self.blocked_status_ids(account_id)
            for task, project, _program in rows:
                if visible is not None and task.project_id not in visible:
                    continue
                assert task.finish_date is not None  # the query only returns tasks with a finish date
                item = SummaryTask(
                    task_id=task.id,
                    name=_truncate(task.name),
                    project_name=project.name,
                    path=f"/projects/{task.project_id}/sheet?task={task.id}",
                    days=(task.finish_date - local_today).days,
                    blocked=task.status_id in blocked_ids,
                    account_name=self.account_name(account_id),
                )
                if item.days < 0:
                    summary.overdue.append(item)
                elif item.days == 0:
                    summary.due_today.append(item)
                elif item.blocked:
                    summary.blocked.append(item)

        mentions = self.db.scalars(
            select(Notification)
            .where(
                Notification.user_id == user.id,
                Notification.notification_type == NotificationType.MENTION.value,
                Notification.is_read.is_(False),
                Notification.created_at >= now - NEW_MENTION_WINDOW,
            )
            .order_by(Notification.created_at, Notification.id)
        ).all()
        for notification in mentions:
            if notification.id in already_listed or not self.can_email(user, notification):
                continue
            summary.mentions.append(
                DigestEntry(
                    notification=notification,
                    item=describe_item(self.db, notification.entity_type, notification.entity_id),
                    account_name=self.account_name(notification.account_id),
                )
            )
        return summary

    def blocked_status_ids(self, account_id: UUID) -> set[UUID]:
        """Task statuses the summary counts as blocked (it compares the value, ignoring case)."""
        return set(
            self.db.scalars(
                select(OptionValue.id)
                .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
                .where(
                    OptionSet.account_id == account_id,
                    OptionSet.entity_type == "TASK",
                    OptionSet.name == "STATUS",
                    func.upper(OptionValue.value) == "BLOCKED",
                )
            ).all()
        )

    # SES bounce and complaint events ----------------------------------------------------------------------

    def record_ses_event(self, message: dict[str, Any]) -> int:
        """Suppress addresses from an SES event (configuration-set events or identity notifications)."""
        event_type = message.get("eventType") or message.get("notificationType")
        addresses: list[str] = []
        reason = ""
        detail = ""
        if event_type == "Bounce":
            bounce = message.get("bounce") or {}
            # Transient bounces (full mailbox, auto-replies) can succeed later.
            if bounce.get("bounceType") != "Permanent":
                return 0
            reason, detail = "BOUNCE", str(bounce.get("bounceSubType") or "")
            addresses = [r.get("emailAddress", "") for r in bounce.get("bouncedRecipients") or []]
        elif event_type == "Complaint":
            complaint = message.get("complaint") or {}
            reason, detail = "COMPLAINT", str(complaint.get("complaintFeedbackType") or "")
            addresses = [r.get("emailAddress", "") for r in complaint.get("complainedRecipients") or []]
        else:
            return 0
        added = 0
        for address in {a.strip().lower() for a in addresses if a and a.strip()}:
            if self.db.get(EmailSuppression, address) is None:
                self.db.add(EmailSuppression(email=address, reason=reason, detail=detail or None))
                added += 1
        self.db.commit()
        return added
