from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.account import Account
from app.models.account_member import AccountMemberRole
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.account_settings import AccountSettingsRepository
from app.repositories.accounts import AccountRepository
from app.schemas.account import AccountAdminContact, AccountCreate, AccountRead, AccountUpdate, PlatformAccountRead
from app.schemas.account_user import AccountUserInviteCreate, AccountUserInviteRole
from app.services.account_users import AccountUserService, CognitoInviteError
from app.services.member_resources import ensure_member_resource
from app.services.options import OptionService
from app.services.platform_audit import PlatformAuditAction, record_platform_action


EDIT_ACCOUNT_ROLES = {AccountMemberRole.OWNER.value}


class AccountService:
    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.account_settings = AccountSettingsRepository(db)

    def create_account(self, *, account_in: AccountCreate, current_user: User) -> Account:
        require_platform_admin(current_user)
        try:
            account = self.accounts.create(
                name=account_in.name,
                slug=account_in.slug,
                created_by=current_user.id,
            )
            if account_in.admin_email:
                # Also sends the Cognito invite when invites are enabled.
                AccountUserService(self.db, settings=self.settings).apply_account_user_invite(
                    account_id=account.id,
                    invite_in=AccountUserInviteCreate(
                        email=account_in.admin_email,
                        full_name=account_in.admin_full_name,
                        role=AccountUserInviteRole.OWNER,
                    ),
                )
            else:
                self.account_members.create(
                    account_id=account.id,
                    user_id=current_user.id,
                    role=AccountMemberRole.OWNER,
                )
                ensure_member_resource(self.db, account_id=account.id, user=current_user)
            self.account_settings.create_default(account.id)
            OptionService(self.db).seed_defaults_for_account(account.id)
            record_platform_action(
                self.db,
                actor=current_user,
                action=PlatformAuditAction.ACCOUNT_CREATED,
                account=account,
                target=account_in.admin_email,
                details={"slug": account.slug},
            )
            self.db.commit()
            self.db.refresh(account)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account slug already exists or membership already exists.",
            ) from exc
        except CognitoInviteError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cognito invite failed.",
            ) from exc
        return account

    def list_accounts_for_user(self, *, current_user: User) -> list[AccountRead]:
        accounts = self.accounts.list_for_user(current_user.id)
        roles = self.account_members.roles_for_user(current_user.id)
        return [
            AccountRead.model_validate(account).model_copy(update={"role": roles.get(account.id)})
            for account in accounts
        ]

    def list_all_accounts(self, *, current_user: User) -> list[PlatformAccountRead]:
        require_platform_admin(current_user)
        accounts = self.accounts.list_all()
        member_counts = self.account_members.count_by_account()
        admins: dict[UUID, list[AccountAdminContact]] = {}
        for account_id, user, role in self.account_members.list_admins():
            admins.setdefault(account_id, []).append(
                AccountAdminContact(email=user.email, full_name=user.full_name, role=AccountMemberRole(role))
            )
        return [
            PlatformAccountRead(
                id=account.id,
                name=account.name,
                slug=account.slug,
                created_at=account.created_at,
                member_count=member_counts.get(account.id, 0),
                admins=admins.get(account.id, []),
            )
            for account in accounts
        ]

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

def require_platform_admin(user: User) -> None:
    if not user.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform admin access required.")
