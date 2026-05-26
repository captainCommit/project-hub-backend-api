import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.comment import Comment
from app.models.comment_mention import CommentMention
from app.models.notification import NotificationType
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.services.notifications import NotificationService


MENTION_TRAILING_BOUNDARY = r"(?=$|[\s.,;:!?()\[\]{}<>\"'])"


class MentionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)

    def list_comment_mentions(self, *, comment_id: UUID, current_user: User) -> list[CommentMention]:
        comment = self.get_comment_or_404(comment_id)
        self.require_account_member(account_id=comment.account_id, user_id=current_user.id)
        return list(
            self.db.scalars(
                select(CommentMention)
                .where(CommentMention.comment_id == comment.id)
                .order_by(CommentMention.created_at, CommentMention.id)
            ).all()
        )

    def sync_comment_mentions(self, *, comment: Comment, actor_user_id: UUID | None) -> list[CommentMention]:
        mentioned_user_ids = self.find_mentioned_user_ids(account_id=comment.account_id, body=comment.body)
        existing_mentions = list(
            self.db.scalars(select(CommentMention).where(CommentMention.comment_id == comment.id)).all()
        )
        existing_user_ids = {mention.mentioned_user_id for mention in existing_mentions}

        for mention in existing_mentions:
            if mention.mentioned_user_id not in mentioned_user_ids:
                self.db.delete(mention)

        new_mentions: list[CommentMention] = []
        notification_service = NotificationService(self.db)
        for mentioned_user_id in sorted(mentioned_user_ids - existing_user_ids, key=str):
            mention = CommentMention(
                account_id=comment.account_id,
                comment_id=comment.id,
                mentioned_user_id=mentioned_user_id,
            )
            self.db.add(mention)
            self.db.flush()
            self.db.refresh(mention)
            new_mentions.append(mention)
            notification_service.create_notification(
                account_id=comment.account_id,
                user_id=mentioned_user_id,
                entity_type="COMMENT",
                entity_id=comment.id,
                notification_type=NotificationType.MENTION,
                title="You were mentioned",
                message=self.mention_message(comment),
                actor_user_id=actor_user_id,
            )
        return new_mentions

    def find_mentioned_user_ids(self, *, account_id: UUID, body: str) -> set[UUID]:
        mentioned_user_ids: set[UUID] = set()
        for user in self.account_members.list_users_for_account(account_id):
            if self.body_mentions_value(body, user.email):
                mentioned_user_ids.add(user.id)
                continue
            if user.full_name and self.body_mentions_value(body, user.full_name):
                mentioned_user_ids.add(user.id)
        return mentioned_user_ids

    def body_mentions_value(self, body: str, value: str) -> bool:
        mention_value = value.strip()
        if not mention_value:
            return False
        pattern = rf"(?<![\w@.])@{re.escape(mention_value)}{MENTION_TRAILING_BOUNDARY}"
        return re.search(pattern, body, flags=re.IGNORECASE) is not None

    def mention_message(self, comment: Comment) -> str:
        return f"You were mentioned in a {comment.entity_type.lower()} comment."

    def get_comment_or_404(self, comment_id: UUID) -> Comment:
        comment = self.db.get(Comment, comment_id)
        if comment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
        return comment

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")