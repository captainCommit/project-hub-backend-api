from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement
from app.models.account_member import AccountMember
from app.models.user import User


class AccountUserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_statement(self, account_id: UUID) -> Select[tuple[User]]:
        return (
            select(User)
            .join(AccountMember, AccountMember.user_id == User.id)
            .where(AccountMember.account_id == account_id)
            .order_by(User.full_name, User.email, User.id)
        )

    def list_for_account(self, account_id: UUID) -> list[User]:
        return list(self.db.scalars(self.list_statement(account_id)).all())

    def list_for_account_paginated(
        self,
        account_id: UUID,
        *,
        pagination: PaginationParams,
    ) -> tuple[list[User], int]:
        return paginate_statement(self.db, self.list_statement(account_id), pagination)

    def search_for_account(self, account_id: UUID, *, query: str) -> list[User]:
        pattern = f"%{query}%"
        statement = self.list_statement(account_id).where(
            or_(
                User.email.ilike(pattern),
                User.full_name.ilike(pattern),
            )
        )
        return list(self.db.scalars(statement).all())