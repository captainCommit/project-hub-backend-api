from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account_member import AccountMember, AccountMemberRole
from app.models.user import User


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

    def update_role(self, account_member: AccountMember, *, role: AccountMemberRole) -> AccountMember:
        account_member.role = role.value
        self.db.add(account_member)
        self.db.flush()
        self.db.refresh(account_member)
        return account_member

    def list_for_account(self, account_id: UUID) -> list[AccountMember]:
        statement = select(AccountMember).where(AccountMember.account_id == account_id)
        return list(self.db.scalars(statement).all())

    def list_users_for_account(self, account_id: UUID) -> list[User]:
        statement = (
            select(User)
            .join(AccountMember, AccountMember.user_id == User.id)
            .where(AccountMember.account_id == account_id)
        )
        return list(self.db.scalars(statement).all())