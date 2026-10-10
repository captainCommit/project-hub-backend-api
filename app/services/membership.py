from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account_member import AccountMember, AccountMemberRole
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository


WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}
ADMIN_ROLES = {AccountMemberRole.OWNER.value, AccountMemberRole.ADMIN.value}


def require_account_member(db: Session, *, account_id: UUID, user_id: UUID) -> AccountMember:
    if AccountRepository(db).get_by_id(account_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    membership = AccountMemberRepository(db).get_for_user(account_id=account_id, user_id=user_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")
    return membership


def require_account_role(db: Session, *, account_id: UUID, user_id: UUID, allowed_roles: set[str]) -> AccountMember:
    membership = require_account_member(db, account_id=account_id, user_id=user_id)
    if membership.role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")
    return membership
