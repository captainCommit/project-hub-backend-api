from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_member import AccountMemberRole
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.schemas.account import AccountCreate, AccountUpdate
from app.services.options import OptionService


EDIT_ACCOUNT_ROLES = {AccountMemberRole.OWNER.value, AccountMemberRole.ADMIN.value}


class AccountService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)

    def create_account(self, *, account_in: AccountCreate, current_user: User) -> Account:
        try:
            account = self.accounts.create(
                name=account_in.name,
                slug=account_in.slug,
                created_by=current_user.id,
            )
            self.account_members.create(
                account_id=account.id,
                user_id=current_user.id,
                role=AccountMemberRole.OWNER,
            )
            OptionService(self.db).seed_defaults_for_account(account.id)
            self.db.commit()
            self.db.refresh(account)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account slug already exists or membership already exists.",
            ) from exc
        return account

    def list_accounts_for_user(self, *, current_user: User) -> list[Account]:
        return self.accounts.list_for_user(current_user.id)

    def get_account_for_user(self, *, account_id: UUID, current_user: User) -> Account:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

        self.require_account_member(account_id=account_id, user_id=current_user.id)
        return account

    def update_account(
        self,
        *,
        account_id: UUID,
        account_in: AccountUpdate,
        current_user: User,
    ) -> Account:
        account = self.get_account_for_user(account_id=account_id, current_user=current_user)
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=EDIT_ACCOUNT_ROLES,
        )

        try:
            updated_account = self.accounts.update(
                account,
                name=account_in.name,
                slug=account_in.slug,
            )
            self.db.commit()
            self.db.refresh(updated_account)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account slug already exists.",
            ) from exc
        return updated_account

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")

    def require_account_role(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        allowed_roles: set[str],
    ) -> None:
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")
        if membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")