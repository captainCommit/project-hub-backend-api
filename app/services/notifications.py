from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.notification import EmailStatus, Notification, NotificationType
from app.models.notification_preference import EmailFrequency
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.services.access import can_see_entity
from app.services.notification_preferences import NotificationPreferenceService


class NotificationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.account_members = AccountMemberRepository(db)
        self.preferences = NotificationPreferenceService(db)

    def create_notification(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        entity_type: str,
        entity_id: UUID,
        notification_type: NotificationType | str,
        title: str,
        message: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> Notification | None:
        if actor_user_id is not None and user_id == actor_user_id:
            return None
        if self.account_members.get_for_user(account_id=account_id, user_id=user_id) is None:
            return None

        type_value = notification_type.value if isinstance(notification_type, NotificationType) else notification_type
        notification = Notification(
            account_id=account_id,
            user_id=user_id,
            entity_type=entity_type.strip().upper(),
            entity_id=entity_id,
            notification_type=type_value,
            title=title,
            message=message,
            email_status=self.initial_email_status(user_id, type_value),
        )
        self.db.add(notification)
        self.db.flush()
        self.db.refresh(notification)
        return notification

    def initial_email_status(self, user_id: UUID, notification_type: str) -> str | None:
        """Queue the email channel. The delivery worker sends it after the request commits (app/worker.py)."""
        if not get_settings().email_enabled:
            return None
        frequency = self.preferences.frequency_for(user_id, notification_type)
        if frequency == EmailFrequency.IMMEDIATE:
            return EmailStatus.PENDING.value
        if frequency == EmailFrequency.DIGEST:
            return EmailStatus.DIGEST.value
        return None

    def create_for_account_members(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        entity_id: UUID,
        notification_type: NotificationType | str,
        title: str,
        message: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> list[Notification]:
        notifications: list[Notification] = []
        seen_user_ids: set[UUID] = set()
        for member in self.account_members.list_for_account(account_id):
            if member.user_id in seen_user_ids:
                continue
            seen_user_ids.add(member.user_id)
            recipient = self.db.get(User, member.user_id)
            if recipient is None or not can_see_entity(
                self.db, account_id=account_id, user=recipient, entity_type=entity_type, entity_id=entity_id
            ):
                # Only people who can open the item hear about it.
                continue
            notification = self.create_notification(
                account_id=account_id,
                user_id=member.user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                notification_type=notification_type,
                title=title,
                message=message,
                actor_user_id=actor_user_id,
            )
            if notification is not None:
                notifications.append(notification)
        return notifications

    def list_notifications(
        self,
        *,
        current_user: User,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, object]:
        statement = select(Notification).where(Notification.user_id == current_user.id)
        if unread_only:
            statement = statement.where(Notification.is_read.is_(False))

        total = int(self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        notifications = list(
            self.db.scalars(
                statement.order_by(Notification.created_at.desc(), Notification.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return {
            "results": notifications,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def mark_read(self, *, notification_id: UUID, current_user: User) -> Notification:
        notification = self.db.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == current_user.id,
            )
        )
        if notification is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
        notification.is_read = True
        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)
        return notification

    def mark_all_read(self, *, current_user: User) -> dict[str, int]:
        notifications = list(
            self.db.scalars(
                select(Notification).where(
                    Notification.user_id == current_user.id,
                    Notification.is_read.is_(False),
                )
            ).all()
        )
        for notification in notifications:
            notification.is_read = True
            self.db.add(notification)
        self.db.commit()
        return {"updated": len(notifications)}