from uuid import UUID

from sqlalchemy import func, select
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
    def roles_for_user(self, user_id: UUID) -> dict[UUID, str]:
        statement = select(AccountMember.account_id, AccountMember.role).where(AccountMember.user_id == user_id)
        return {account_id: role for account_id, role in self.db.execute(statement).all()}

    def count_by_account(self) -> dict[UUID, int]:
        statement = select(AccountMember.account_id, func.count()).group_by(AccountMember.account_id)
        return {account_id: count for account_id, count in self.db.execute(statement).all()}

    def list_admins(self) -> list[tuple[UUID, User, str]]:
        """Owners and admins of every account, owners first."""
        statement = (
            select(AccountMember.account_id, User, AccountMember.role)
            .join(User, User.id == AccountMember.user_id)
            .where(AccountMember.role.in_([AccountMemberRole.OWNER.value, AccountMemberRole.ADMIN.value]))
            .order_by(AccountMember.role.desc(), User.email)
        )
        return [(account_id, user, role) for account_id, user, role in self.db.execute(statement).all()]
