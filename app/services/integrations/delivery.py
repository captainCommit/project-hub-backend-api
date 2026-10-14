"""Sending the outbox to Slack (the integrations worker, every minute).

Rows are claimed in batches so overlapping runs don't send twice. Each is rendered at send time, as the person
it is checked against, so a change of access between queueing and sending is respected. A token the provider no
longer accepts marks the whole connection as needing attention; other failures are retried a few times.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.activity_log import ActivityLog
from app.models.integration import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationDestination,
    IntegrationMessage,
    IntegrationUserLink,
    MessageStatus,
)
from app.models.notification import Notification
from app.models.user import User
from app.services.integrations.http import Http, requests_http
from app.services.integrations.messages import ChatMessage, to_slack_blocks
from app.services.integrations.render import render_activity, render_notification, render_overdue
from app.services.integrations.secrets import TokenCipher
from app.services.integrations.slack import SlackClient, SlackError
from app.services.notification_preferences import category_for

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
CLAIM_TIMEOUT = timedelta(minutes=10)
# Messages older than this are dropped rather than posted late (a channel full of day-old events is noise).
MAX_AGE = timedelta(hours=6)


class SendFailed(Exception):
    def __init__(self, message: str, *, retry: bool, connection_broken: bool = False) -> None:
        super().__init__(message)
        self.retry = retry
        self.connection_broken = connection_broken


class IntegrationDelivery:
    def __init__(self, db: Session, settings: Settings, *, http: Http = requests_http, cipher_factory: Callable[[Settings], TokenCipher] = TokenCipher) -> None:
        self.db = db
        self.settings = settings
        self.http = http
        self.cipher_factory = cipher_factory
        self._slack: dict[UUID, SlackClient] = {}

    def deliver_pending(self, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
        now = now or datetime.now(UTC)
        counts = {"sent": 0, "skipped": 0, "failed": 0, "retry": 0}
        for message_id in self.claim(now=now, limit=limit):
            message = self.db.get(IntegrationMessage, message_id)
            if message is not None:
                counts[self.deliver_one(message, now=now)] += 1
        return counts

    def claim(self, *, now: datetime, limit: int) -> list[UUID]:
        statement = (
            select(IntegrationMessage.id)
            .where(
                or_(
                    IntegrationMessage.status == MessageStatus.PENDING.value,
                    and_(IntegrationMessage.status == MessageStatus.SENDING.value, IntegrationMessage.claimed_at < now - CLAIM_TIMEOUT),
                )
            )
            .order_by(IntegrationMessage.created_at, IntegrationMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        ids = list(self.db.scalars(statement).all())
        if ids:
            self.db.execute(
                update(IntegrationMessage)
                .where(IntegrationMessage.id.in_(ids))
                .values(status=MessageStatus.SENDING.value, claimed_at=now)
            )
        self.db.commit()
        return ids

    def deliver_one(self, message: IntegrationMessage, *, now: datetime) -> str:
        created = message.created_at if message.created_at.tzinfo else message.created_at.replace(tzinfo=UTC)
        if now - created > MAX_AGE:
            return self.finish(message, MessageStatus.SKIPPED, "Too old to post.")
        try:
            target = self.prepare(message)
            if target is None:
                return self.finish(message, MessageStatus.SKIPPED)
            connection, chat, send = target
            send(chat)
        except SendFailed as failure:
            if failure.connection_broken:
                self.mark_broken(message, str(failure))
            message.attempts += 1
            message.claimed_at = None
            retry = failure.retry and message.attempts < MAX_ATTEMPTS
            if not retry:
                logger.warning("Integration message failed", extra={"message_id": str(message.id), "error": str(failure)})
            return self.finish(message, MessageStatus.PENDING if retry else MessageStatus.FAILED, str(failure), counted="retry" if retry else "failed")
        except Exception:
            self.db.rollback()
            logger.exception("Integration message failed", extra={"message_id": str(message.id)})
            message = self.db.get(IntegrationMessage, message.id)  # type: ignore[assignment]
            message.attempts += 1
            retry = message.attempts < MAX_ATTEMPTS
            return self.finish(message, MessageStatus.PENDING if retry else MessageStatus.FAILED, "Unexpected error.", counted="retry" if retry else "failed")
        message.sent_at = now
        return self.finish(message, MessageStatus.SENT)

    def finish(self, message: IntegrationMessage, status: MessageStatus, error: str | None = None, *, counted: str | None = None) -> str:
        message.status = status.value
        message.error = error[:500] if error else None
        self.db.commit()
        return counted or status.value.lower()

    # What to send, and where

    def prepare(self, message: IntegrationMessage) -> tuple[IntegrationConnection, ChatMessage, Callable[[ChatMessage], None]] | None:
        if message.destination_id:
            destination = self.db.get(IntegrationDestination, message.destination_id)
            if destination is None or not destination.is_active:
                return None
            connection = self.db.get(IntegrationConnection, destination.connection_id)
            viewer = self.db.get(User, destination.created_by) if destination.created_by else None
            if connection is None or connection.status != ConnectionStatus.ACTIVE.value or viewer is None:
                return None
            if message.kind == "OVERDUE":
                chat = render_overdue(self.db, destination, viewer)
            else:
                activity = self.db.get(ActivityLog, message.activity_id) if message.activity_id else None
                chat = render_activity(self.db, activity, message.event or "", viewer) if activity else None
            if chat is None:
                return None
            return connection, chat, lambda c: self.to_channel(connection, destination.channel_id, c)

        link = self.db.get(IntegrationUserLink, message.user_link_id) if message.user_link_id else None
        notification = self.db.get(Notification, message.notification_id) if message.notification_id else None
        if link is None or notification is None or notification.user_id != link.user_id:
            return None
        if category_for(notification.notification_type) not in (link.dm_categories or []):
            return None
        connection = self.db.get(IntegrationConnection, link.connection_id)
        user = self.db.get(User, link.user_id)
        if connection is None or connection.status != ConnectionStatus.ACTIVE.value or user is None:
            return None
        chat = render_notification(self.db, notification, user)
        if chat is None:
            return None
        return connection, chat, lambda c: self.to_person(connection, link, c)

    def to_channel(self, connection: IntegrationConnection, channel_id: str, chat: ChatMessage) -> None:
        self.slack_send(connection, channel_id, chat)

    def to_person(self, connection: IntegrationConnection, link: IntegrationUserLink, chat: ChatMessage) -> None:
        try:
            channel = self.slack(connection).open_dm(link.external_user_id)
        except SlackError as error:
            raise SendFailed(f"Slack: {error.error}", retry=not error.is_auth, connection_broken=error.is_auth) from error
        self.slack_send(connection, channel, chat)

    def slack(self, connection: IntegrationConnection) -> SlackClient:
        if connection.id not in self._slack:
            if not connection.encrypted_token:
                raise SendFailed("The Slack connection has no token.", retry=False, connection_broken=True)
            token = self.cipher_factory(self.settings).decrypt(connection.account_id, connection.encrypted_token)
            self._slack[connection.id] = SlackClient(token, self.http)
        return self._slack[connection.id]

    def slack_send(self, connection: IntegrationConnection, channel: str, chat: ChatMessage) -> None:
        try:
            self.slack(connection).post_message(channel, text=chat.plain_text(self.settings), blocks=to_slack_blocks(chat, self.settings))
        except SlackError as error:
            gone = error.error in ("channel_not_found", "is_archived", "not_in_channel")
            raise SendFailed(f"Slack: {error.error}", retry=not (error.is_auth or gone), connection_broken=error.is_auth) from error

    def mark_broken(self, message: IntegrationMessage, reason: str) -> None:
        connection_id = None
        if message.destination_id:
            destination = self.db.get(IntegrationDestination, message.destination_id)
            connection_id = destination.connection_id if destination else None
        elif message.user_link_id:
            link = self.db.get(IntegrationUserLink, message.user_link_id)
            connection_id = link.connection_id if link else None
        connection = self.db.get(IntegrationConnection, connection_id) if connection_id else None
        if connection is not None:
            connection.status = ConnectionStatus.ERROR.value
            connection.last_error = reason[:500]
