"""Slack, Microsoft Teams and Microsoft 365 connections, and what they deliver.

- IntegrationConnection: an account's Slack workspace, Teams tenant, or Microsoft 365 mailbox for sending email.
- IntegrationConversation: a Teams channel or personal chat the bot has been added to (Slack channels are listed
  live from Slack instead, so they aren't stored).
- IntegrationDestination: a channel that receives chosen events for one portfolio, program or project. Slack and
  Teams share it. Events are checked at send time against what the person who set it up can see.
- IntegrationUserLink: a Project Hub person linked to their Slack or Teams user, for direct messages and commands.
- IntegrationMessage: the outbox. Requests and the event scanner only add rows; the worker sends them.
- CalendarFeed / MicrosoftCalendarConnection / CalendarSyncEvent: Outlook (and any calendar app) seeing the
  person's due dates and milestones.

Tokens are only ever stored encrypted (app.services.integrations.secrets).
"""

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class IntegrationProvider(str, Enum):
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    # A Microsoft 365 mailbox that sends the account's notification email.
    MICROSOFT_MAIL = "MICROSOFT_MAIL"


class ConnectionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    # The provider refused the token (revoked, uninstalled, expired); an owner has to connect again.
    ERROR = "ERROR"


PROVIDERS_SQL = "('SLACK', 'TEAMS', 'MICROSOFT_MAIL')"


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        CheckConstraint(f"provider in {PROVIDERS_SQL}", name="ck_integration_connections_provider"),
        CheckConstraint("status in ('ACTIVE', 'ERROR')", name="ck_integration_connections_status"),
        UniqueConstraint("account_id", "provider", name="uq_integration_connections_account_provider"),
        Index("ix_integration_connections_provider_external", "provider", "external_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=ConnectionStatus.ACTIVE.value)
    # Slack team id, Teams tenant id, or the mailbox's Microsoft user id.
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # Workspace name, tenant name, or mailbox address.
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Slack bot token or Microsoft refresh token. Teams uses the platform's bot credentials, so has none.
    encrypted_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Provider details worth keeping (Slack bot user id, granted scopes, ...).
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    connected_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now(), onupdate=_now
    )


class IntegrationConversation(Base):
    """A Teams conversation the bot can post to: a team channel, or one person's chat with the bot."""

    __tablename__ = "integration_conversations"
    __table_args__ = (
        CheckConstraint("kind in ('CHANNEL', 'PERSONAL')", name="ck_integration_conversations_kind"),
        UniqueConstraint("provider", "external_id", name="uq_integration_conversations_provider_external"),
        Index("ix_integration_conversations_tenant", "provider", "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default=IntegrationProvider.TEAMS.value)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # The conversation id messages are posted to.
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    service_url: Mapped[str] = mapped_column(String(500), nullable=False)
    team_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # PERSONAL only: the person's Microsoft Entra object id.
    user_external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # False once the bot is removed from the team.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now(), onupdate=_now
    )


# Events a channel can receive. Keys are stored in IntegrationDestination.events.
CHANNEL_EVENTS: dict[str, str] = {
    "task_created": "New tasks",
    "task_status_changed": "Task status changes",
    "task_overdue": "Overdue tasks (daily summary)",
    "risk_created": "New risks",
    "risk_created_high": "New high or critical risks only",
    "issue_created": "New issues",
    "decision_changed": "Decision status changes",
    "comment_added": "New comments",
    "project_status_changed": "Project status changes",
}


class IntegrationDestination(Base):
    __tablename__ = "integration_destinations"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN portfolio_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN program_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN project_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_integration_destinations_one_scope",
        ),
        Index("ix_integration_destinations_account", "account_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=False
    )
    # Slack channel id, or the Teams conversation id.
    channel_id: Mapped[str] = mapped_column(String(300), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    portfolio_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True)
    program_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE"), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Only events this person can see are posted; if they leave the account, nothing is.
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Local date (in the creator's timezone) of the last overdue summary, so it goes out once a day.
    last_overdue_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


# Notification categories (app.services.notification_preferences) a person can get as direct messages.
DEFAULT_DM_CATEGORIES = ["mentions", "assignments"]


class IntegrationUserLink(Base):
    __tablename__ = "integration_user_links"
    __table_args__ = (
        UniqueConstraint("connection_id", "user_id", name="uq_integration_user_links_connection_user"),
        UniqueConstraint("connection_id", "external_user_id", name="uq_integration_user_links_connection_external"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Slack user id, or Microsoft Entra object id.
    external_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Teams: the personal chat with the bot, where direct messages go.
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integration_conversations.id", ondelete="SET NULL"), nullable=True
    )
    dm_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=lambda: list(DEFAULT_DM_CATEGORIES))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class MessageStatus(str, Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    # Dropped at send time: no longer visible, turned off, or the connection is gone.
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class IntegrationMessage(Base):
    """One message to send: an activity to a channel, a notification to a person, or a channel's overdue summary."""

    __tablename__ = "integration_messages"
    __table_args__ = (
        CheckConstraint("kind in ('ACTIVITY', 'NOTIFICATION', 'OVERDUE')", name="ck_integration_messages_kind"),
        CheckConstraint(
            "status in ('PENDING', 'SENDING', 'SENT', 'SKIPPED', 'FAILED')", name="ck_integration_messages_status"
        ),
        UniqueConstraint("destination_id", "activity_id", name="uq_integration_messages_destination_activity"),
        UniqueConstraint("user_link_id", "notification_id", name="uq_integration_messages_link_notification"),
        Index("ix_integration_messages_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    destination_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integration_destinations.id", ondelete="CASCADE"), nullable=True
    )
    user_link_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integration_user_links.id", ondelete="CASCADE"), nullable=True
    )
    activity_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("activity_log.id", ondelete="CASCADE"), nullable=True)
    notification_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE"), nullable=True
    )
    # Event key (CHANNEL_EVENTS) for channel messages.
    event: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=MessageStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class IntegrationState(Base):
    """Small bits of worker state, such as how far the activity log has been scanned."""

    __tablename__ = "integration_state"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now(), onupdate=_now
    )


# Calendars -------------------------------------------------------------------------------------------------


class CalendarFeed(Base):
    """A person's private calendar subscription (ICS) URL. Only a hash of its token is kept."""

    __tablename__ = "calendar_feeds"

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MicrosoftCalendarConnection(Base):
    """A person's Outlook calendar, kept up to date with their due dates and milestones (one way: into Outlook)."""

    __tablename__ = "microsoft_calendar_connections"
    __table_args__ = (CheckConstraint("status in ('ACTIVE', 'ERROR')", name="ck_microsoft_calendar_connections_status"),)

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    microsoft_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The "Project Hub" calendar created in their mailbox; only this calendar is ever written to.
    calendar_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=ConnectionStatus.ACTIVE.value)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, server_default=func.now())


class CalendarSyncEvent(Base):
    """An Outlook event written for one task or milestone, and a hash of what it said, to update only on change."""

    __tablename__ = "calendar_sync_events"
    __table_args__ = (UniqueConstraint("user_id", "source_key", name="uq_calendar_sync_events_user_source"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # "task:<id>" (the same key as the ICS feed's UID).
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now(), onupdate=_now
    )
