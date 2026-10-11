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

    def count_owners(self, account_id: UUID) -> int:
        statement = select(func.count()).where(
            AccountMember.account_id == account_id,
            AccountMember.role == AccountMemberRole.OWNER.value,
        )
        return self.db.scalar(statement) or 0

    def count_by_account(self) -> dict[UUID, int]:
        statement = select(AccountMember.account_id, func.count()).group_by(AccountMember.account_id)
        return {account_id: count for account_id, count in self.db.execute(statement).all()}

    def count_seats_by_account_role(self) -> dict[UUID, dict[str, int]]:
        """Active members who have signed in, per account and role. Pending invites hold no seat."""
        statement = (
            select(AccountMember.account_id, AccountMember.role, func.count())
            .join(User, User.id == AccountMember.user_id)
            .where(User.cognito_sub.is_not(None), User.is_active.is_(True))
            .group_by(AccountMember.account_id, AccountMember.role)
        )
        counts: dict[UUID, dict[str, int]] = {}
        for account_id, role, count in self.db.execute(statement).all():
            counts.setdefault(account_id, {})[role] = count
        return counts

    def count_pending_invites_by_account(self) -> dict[UUID, int]:
        statement = (
            select(AccountMember.account_id, func.count())
            .join(User, User.id == AccountMember.user_id)
            .where(User.cognito_sub.is_(None))
            .group_by(AccountMember.account_id)
        )
        return {account_id: count for account_id, count in self.db.execute(statement).all()}

    def list_admins(self) -> list[tuple[UUID, User, str]]:
        """Owners of every account."""
        statement = (
            select(AccountMember.account_id, User, AccountMember.role)
            .join(User, User.id == AccountMember.user_id)
            .where(AccountMember.role == AccountMemberRole.OWNER.value)
            .order_by(User.email)
        )
        return [(account_id, user, role) for account_id, user, role in self.db.execute(statement).all()]
