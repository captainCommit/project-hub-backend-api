from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.pagination import PaginationParams, paginated_response
from app.models.access_grant import GrantRole
from app.models.account import Account
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.account_users import AccountUserRepository
from app.repositories.accounts import AccountRepository
from app.repositories.users import UserRepository
from app.schemas.account_user import (
    AccountUserBulkInviteCreate,
    AccountUserBulkInviteRead,
    AccountUserBulkInviteResult,
    AccountUserInviteCreate,
    AccountUserInviteRead,
    AccountUserInviteStatus,
    AccountUserRead,
)
from app.services.access import forget_access
from app.services.access_grants import AccessGrantService
from app.services.billing import BillingService
from app.services.cognito import admin_create_user_invite, should_send_cognito_invite
from app.services.platform_audit import PlatformAuditAction, record_platform_action


INVITE_ACCOUNT_ROLES = {AccountMemberRole.OWNER.value}


class CognitoInviteError(Exception):
    """Raised when Cognito AdminCreateUser fails during account-user onboarding."""


class LastOwnerError(Exception):
    """Changing this role would leave the account without an owner."""


LAST_OWNER_MESSAGE = "An account needs at least one owner."


class AccountUserService:
    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.account_users = AccountUserRepository(db)
        self.users = UserRepository(db)

    def list_account_users(
        self,
        *,
        account_id: UUID,
        current_user: User,
        pagination: PaginationParams | None = None,
    ) -> list[AccountUserRead] | dict[str, object]:
        platform_account = self.require_account_access(account_id=account_id, current_user=current_user)
        if platform_account is not None:
            record_platform_action(
                self.db, actor=current_user, action=PlatformAuditAction.ACCOUNT_USERS_VIEWED, account=platform_account
            )
            self.db.commit()
        if pagination and pagination.paginated:
            users, total = self.account_users.list_for_account_paginated(account_id, pagination=pagination)
            return paginated_response(items=self.to_reads(account_id, users), total=total, pagination=pagination)
        return self.to_reads(account_id, self.account_users.list_for_account(account_id))

    def to_reads(self, account_id: UUID, users: list[User]) -> list[AccountUserRead]:
        roles = {member.user_id: member.role for member in self.account_members.list_for_account(account_id)}
        return [
            AccountUserRead.model_validate(user).model_copy(
                update={"role": roles.get(user.id), "has_signed_in": user.cognito_sub is not None}
            )
            for user in users
        ]

    def search_account_users(self, *, account_id: UUID, q: str, current_user: User) -> list[User]:
        query = q.strip()
        if len(query) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Search query must be at least 2 characters.",
            )
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        return self.account_users.search_for_account(account_id, query=query)

    def invite_account_user(
        self,
        *,
        account_id: UUID,
        invite_in: AccountUserInviteCreate,
        current_user: User,
    ) -> AccountUserInviteRead:
        platform_account = self.require_account_access(
            account_id=account_id,
            current_user=current_user,
            allowed_roles=INVITE_ACCOUNT_ROLES,
        )
        try:
            result = self.apply_account_user_invite(account_id=account_id, invite_in=invite_in)
            if platform_account is not None and result.status != AccountUserInviteStatus.ALREADY_EXISTS:
                record_platform_action(
                    self.db,
                    actor=current_user,
                    action=(
                        PlatformAuditAction.ACCOUNT_USER_INVITED
                        if result.status == AccountUserInviteStatus.CREATED
                        else PlatformAuditAction.ACCOUNT_USER_ROLE_CHANGED
                    ),
                    account=platform_account,
                    target=result.email,
                    details={"role": result.role.value},
                )
            self.db.commit()
            self.sync_billing(account_id, changed=result.status != AccountUserInviteStatus.ALREADY_EXISTS)
            return result
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User or account membership already exists.",
            ) from exc
        except CognitoInviteError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cognito invite failed.",
            ) from exc
        except LastOwnerError as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_OWNER_MESSAGE) from exc

    def bulk_invite_account_users(
        self,
        *,
        account_id: UUID,
        bulk_in: AccountUserBulkInviteCreate,
        current_user: User,
    ) -> AccountUserBulkInviteRead:
        platform_account = self.require_account_access(
            account_id=account_id,
            current_user=current_user,
            allowed_roles=INVITE_ACCOUNT_ROLES,
        )

        counts = {
            AccountUserInviteStatus.CREATED: 0,
            AccountUserInviteStatus.UPDATED: 0,
            AccountUserInviteStatus.ALREADY_EXISTS: 0,
            AccountUserInviteStatus.FAILED: 0,
        }
        results: list[AccountUserBulkInviteResult] = []

        for item in bulk_in.users:
            payload = item.model_dump()
            if payload.get("update_existing") is None:
                payload["update_existing"] = bulk_in.update_existing

            try:
                invite_in = AccountUserInviteCreate.model_validate(payload)
                invite_result = self.apply_account_user_invite(account_id=account_id, invite_in=invite_in)
                self.db.commit()
                counts[invite_result.status] += 1
                results.append(
                    AccountUserBulkInviteResult(
                        email=invite_result.email,
                        status=invite_result.status,
                        user_id=invite_result.user_id,
                        role=invite_result.role,
                        error=None,
                    )
                )
            except ValidationError as exc:
                self.db.rollback()
                counts[AccountUserInviteStatus.FAILED] += 1
                results.append(
                    AccountUserBulkInviteResult(
                        email=self.safe_email_value(payload.get("email")),
                        status=AccountUserInviteStatus.FAILED,
                        role=None,
                        error=self.validation_error_message(exc),
                    )
                )
            except IntegrityError:
                self.db.rollback()
                counts[AccountUserInviteStatus.FAILED] += 1
                results.append(
                    AccountUserBulkInviteResult(
                        email=self.safe_email_value(payload.get("email")),
                        status=AccountUserInviteStatus.FAILED,
                        role=None,
                        error="User or account membership already exists.",
                    )
                )
            except CognitoInviteError:
                self.db.rollback()
                counts[AccountUserInviteStatus.FAILED] += 1
                results.append(
                    AccountUserBulkInviteResult(
                        email=self.safe_email_value(payload.get("email")),
                        status=AccountUserInviteStatus.FAILED,
                        role=None,
                        error="Cognito invite failed.",
                    )
                )
            except HTTPException as exc:
                # E.g. the starting access named a project outside the account.
                self.db.rollback()
                counts[AccountUserInviteStatus.FAILED] += 1
                results.append(
                    AccountUserBulkInviteResult(
                        email=self.safe_email_value(payload.get("email")),
                        status=AccountUserInviteStatus.FAILED,
                        role=None,
                        error=str(exc.detail),
                    )
                )
            except LastOwnerError:
                self.db.rollback()
                counts[AccountUserInviteStatus.FAILED] += 1
                results.append(
                    AccountUserBulkInviteResult(
                        email=self.safe_email_value(payload.get("email")),
                        status=AccountUserInviteStatus.FAILED,
                        role=None,
                        error=LAST_OWNER_MESSAGE,
                    )
                )

        changed = [
            {"email": r.email, "status": r.status.value, "role": r.role.value}
            for r in results
            if r.status in (AccountUserInviteStatus.CREATED, AccountUserInviteStatus.UPDATED) and r.role is not None
        ]
        if platform_account is not None and changed:
            record_platform_action(
                self.db,
                actor=current_user,
                action=PlatformAuditAction.ACCOUNT_USERS_BULK_INVITED,
                account=platform_account,
                details={"users": changed},
            )
            self.db.commit()

        self.sync_billing(
            account_id,
            changed=bool(counts[AccountUserInviteStatus.CREATED] or counts[AccountUserInviteStatus.UPDATED]),
        )
        return AccountUserBulkInviteRead(
            created=counts[AccountUserInviteStatus.CREATED],
            updated=counts[AccountUserInviteStatus.UPDATED],
            already_exists=counts[AccountUserInviteStatus.ALREADY_EXISTS],
            failed=counts[AccountUserInviteStatus.FAILED],
            results=results,
        )

    def apply_account_user_invite(self, *, account_id: UUID, invite_in: AccountUserInviteCreate) -> AccountUserInviteRead:
        user = self.users.get_by_email_normalized(invite_in.email)
        if user is None:
            user = self.users.create(email=invite_in.email, full_name=invite_in.full_name)
        elif user.email != invite_in.email:
            user = self.users.update_email(user, email=invite_in.email)

        membership = self.account_members.get_for_user(account_id=account_id, user_id=user.id)
        if membership is None:
            membership = self.account_members.create(
                account_id=account_id,
                user_id=user.id,
                role=AccountMemberRole(invite_in.role.value),
            )
            invite_status = AccountUserInviteStatus.CREATED
        elif membership.role != invite_in.role.value and invite_in.update_existing:
            if (
                membership.role == AccountMemberRole.OWNER.value
                and self.account_members.count_owners(account_id) <= 1
            ):
                raise LastOwnerError
            membership = self.account_members.update_role(
                membership,
                role=AccountMemberRole(invite_in.role.value),
            )
            invite_status = AccountUserInviteStatus.UPDATED
        else:
            invite_status = AccountUserInviteStatus.ALREADY_EXISTS

        if invite_in.access is not None and membership.role != AccountMemberRole.OWNER.value:
            AccessGrantService(self.db).apply_grant(
                account_id=account_id,
                user_id=user.id,
                role=GrantRole(membership.role),
                target=invite_in.access,
                modules=invite_in.access.modules,
                created_by=None,
            )
            forget_access(self.db)

        self.send_cognito_invite_if_enabled(user=user)

        return self.build_invite_read(
            user=user,
            membership=membership,
            status=invite_status,
        )

    def sync_billing(self, account_id: UUID, *, changed: bool) -> None:
        """Someone who has signed in before takes a seat as soon as they join; role changes can move seat types."""
        if changed and self.settings is not None:
            BillingService(self.db, settings=self.settings).sync_seats_quietly([account_id])

    def build_invite_read(
        self,
        *,
        user: User,
        membership: AccountMember,
        status: AccountUserInviteStatus,
    ) -> AccountUserInviteRead:
        return AccountUserInviteRead(
            email=user.email,
            status=status,
            user_id=user.id,
            account_id=membership.account_id,
            membership_id=membership.id,
            full_name=user.full_name,
            role=AccountMemberRole(membership.role),
            error=None,
        )

    def send_cognito_invite_if_enabled(self, *, user: User) -> None:
        if self.settings is None or not should_send_cognito_invite(self.settings):
            return
        if user.cognito_sub:
            return

        try:
            admin_create_user_invite(
                email=user.email,
                full_name=user.full_name,
                settings=self.settings,
            )
        except Exception as exc:
            raise CognitoInviteError from exc

    def validation_error_message(self, exc: ValidationError) -> str:
        first_error = exc.errors()[0] if exc.errors() else {}
        location = first_error.get("loc", ())
        if "email" in location:
            return "Invalid email format."
        if "role" in location:
            return "Invalid role."
        return str(first_error.get("msg") or "Validation error.")

    def safe_email_value(self, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower() or None

    def require_account_access(
        self,
        *,
        account_id: UUID,
        current_user: User,
        allowed_roles: set[str] | None = None,
    ) -> Account | None:
        """Members (holding one of allowed_roles, when given) pass; platform admins pass for any account.

        Returns the account when access came only from platform admin powers, so the caller can audit it.
        """
        membership = self.account_members.get_for_user(account_id=account_id, user_id=current_user.id)
        if membership is not None and (allowed_roles is None or membership.role in allowed_roles):
            return None
        if not current_user.is_platform_admin:
            if allowed_roles is None:
                self.require_account_member(account_id=account_id, user_id=current_user.id)
            else:
                self.require_account_role(account_id=account_id, user_id=current_user.id, allowed_roles=allowed_roles)
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        return account

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")

    def require_account_role(self, *, account_id: UUID, user_id: UUID, allowed_roles: set[str]) -> None:
        self.require_account_member(account_id=account_id, user_id=user_id)
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None or membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")