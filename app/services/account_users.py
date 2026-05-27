from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginated_response
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.account_users import AccountUserRepository
from app.repositories.accounts import AccountRepository


class AccountUserService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.account_users = AccountUserRepository(db)

    def list_account_users(
        self,
        *,
        account_id: UUID,
        current_user: User,
        pagination: PaginationParams | None = None,
    ) -> list[User] | dict[str, object]:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        if pagination and pagination.paginated:
            users, total = self.account_users.list_for_account_paginated(account_id, pagination=pagination)
            return paginated_response(items=users, total=total, pagination=pagination)
        return self.account_users.list_for_account(account_id)

    def search_account_users(self, *, account_id: UUID, q: str, current_user: User) -> list[User]:
        query = q.strip()
        if len(query) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Search query must be at least 2 characters.",
            )
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        return self.account_users.search_for_account(account_id, query=query)

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")