"""Whether an account has the AI assistant at all: a platform admin's switch, which owners can ask for.

Off: Ask Compass, reports and the AI settings tab are unavailable, and the API refuses AI calls for the account.
On: the account sets up its own key or asks for the platform model (account_settings.py, platform.py).
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.user import User
from app.services.access import access_for
from app.services.activity import ActivityLogService
from app.services.platform_audit import record_platform_action

NOT_ENABLED_MESSAGE = "AI isn't turned on for this account. An account owner can request it in Account > Connected apps."


class AiFeatureAction:
    ENABLED = "account.ai_enabled"
    DISABLED = "account.ai_disabled"


class AiFeatureService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def account(self, account_id: UUID) -> Account:
        account = self.db.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        return account

    def is_enabled(self, account_id: UUID) -> bool:
        account = self.db.get(Account, account_id)
        return bool(account and account.ai_enabled)

    def require_enabled(self, account_id: UUID) -> None:
        if not self.is_enabled(account_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_ENABLED_MESSAGE)

    # Owners

    def request(self, *, account_id: UUID, current_user: User) -> None:
        """An owner asks a platform admin to turn AI on. Asking again just refreshes the date."""
        if not access_for(self.db, account_id=account_id, user=current_user).is_owner:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only account owners can request AI.")
        account = self.account(account_id)
        if account.ai_enabled:
            return
        account.ai_requested_at = datetime.now(UTC)
        account.ai_requested_by = current_user.id
        ActivityLogService(self.db).record(
            account_id=account_id, entity_type="ACCOUNT", entity_id=account_id, action="UPDATED",
            new_values={"ai": "requested"}, created_by=current_user.id,
        )
        self.db.commit()

    # Platform admins

    def list_accounts(self) -> list[dict[str, object]]:
        """Every account, requests first, then the ones with AI on, then the rest."""
        accounts = list(self.db.scalars(select(Account).order_by(Account.name)).all())
        rows = [self.account_dict(a) for a in accounts]
        return sorted(rows, key=lambda r: (not (r["requested_at"] and not r["enabled"]), not r["enabled"]))

    def set_enabled(self, *, actor: User, account_id: UUID, enabled: bool) -> dict[str, object]:
        account = self.account(account_id)
        if account.ai_enabled != enabled:
            account.ai_enabled = enabled
            record_platform_action(
                self.db, actor=actor, action=AiFeatureAction.ENABLED if enabled else AiFeatureAction.DISABLED, account=account,
            )
        if enabled:
            account.ai_requested_at = None
            account.ai_requested_by = None
        self.db.commit()
        return self.account_dict(account)

    def account_dict(self, account: Account) -> dict[str, object]:
        requested_by = self.db.get(User, account.ai_requested_by) if account.ai_requested_by else None
        return {
            "account_id": account.id,
            "account_name": account.name,
            "enabled": account.ai_enabled,
            "requested_at": account.ai_requested_at,
            "requested_by_email": requested_by.email if requested_by else None,
        }
