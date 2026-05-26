from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account_member import AccountMember, AccountMemberRole


class AccountMemberRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        role: AccountMemberRole,
    ) -> AccountMember:
        account_member = AccountMember(
            account_id=account_id,
            user_id=user_id,
            role=role.value,
        )
        self.db.add(account_member)
        self.db.flush()
        self.db.refresh(account_member)
        return account_member

    def get_for_user(self, *, account_id: UUID, user_id: UUID) -> AccountMember | None:
        statement = select(AccountMember).where(
            AccountMember.account_id == account_id,
            AccountMember.user_id == user_id,
        )
        return self.db.scalar(statement)