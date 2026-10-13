"""Platform-provided models for accounts without their own key.

An account owner requests the platform model, accepting that usage past the monthly allowance is added to the
bill. A platform admin then switches it on, picking the model, the allowance (tokens or a credit) and an optional
monthly cap. Platform admins see terms and usage numbers only, never conversations or project data.
"""

from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, _read_optional_parameter
from app.models.account import Account
from app.models.ai import (
    AiAccountSettings,
    AiPlatformAccess,
    AiPlatformAccessStatus,
    AiPlatformModel,
    AllowanceKind,
)
from app.models.user import User
from app.schemas.ai import AiPlatformAccessUpdate, AiPlatformModelCreate, AiPlatformModelUpdate
from app.services.access import access_for
from app.services.activity import ActivityLogService
from app.services.ai.providers import Provider, ProviderConfig, ResolvedModel
from app.services.ai.usage import MonthUsage, platform_usage
from app.services.platform_audit import record_platform_action


class PlatformAiAction:
    ENABLED = "account.ai_platform_enabled"
    DISABLED = "account.ai_platform_disabled"
    DECLINED = "account.ai_platform_declined"
    UPDATED = "account.ai_platform_updated"
    MODEL_CREATED = "ai.platform_model_created"
    MODEL_UPDATED = "ai.platform_model_updated"


class PlatformLimitReached(Exception):
    """This month's allowance (or cap) is used up."""


def _env_key(settings: Settings, provider: str) -> str:
    return {
        "OPENAI": settings.ai_platform_openai_api_key,
        "ANTHROPIC": settings.ai_platform_anthropic_api_key,
        "GEMINI": settings.ai_platform_gemini_api_key,
    }.get(provider, "")


@lru_cache(maxsize=8)
def _ssm_key(name: str, region: str) -> str:
    return _read_optional_parameter(name, region)


def platform_api_key(settings: Settings, provider: str) -> str:
    """The platform's own key for a provider: an environment variable locally, an SSM SecureString deployed."""
    key = _env_key(settings, provider)
    if key or not settings.ai_platform_key_param_prefix:
        return key
    return _ssm_key(f"{settings.ai_platform_key_param_prefix}{provider.lower()}-api-key", settings.aws_region)


def charge_rate(model: AiPlatformModel, cost: Decimal) -> Decimal:
    return Decimal(cost) * (Decimal(100) + Decimal(model.margin_percent)) / Decimal(100)


class PlatformAiService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # Everyone: is the platform model usable for this account right now?

    def availability(self, account_id: UUID) -> tuple[str | None, AiPlatformModel | None]:
        """(None, model) when usable; ("NOT_SET_UP" | "LIMIT_REACHED", model) when not."""
        access = self.db.get(AiPlatformAccess, account_id)
        model = self.db.get(AiPlatformModel, access.platform_model_id) if access and access.platform_model_id else None
        if access is None or access.status != AiPlatformAccessStatus.ENABLED.value or model is None or not model.is_active:
            return "NOT_SET_UP", None
        if platform_usage(self.db, self.settings, access).limit_reached:
            return "LIMIT_REACHED", model
        return None, model

    # The worker

    def resolve(self, account_id: UUID) -> ResolvedModel | None:
        reason, model = self.availability(account_id)
        if reason == "LIMIT_REACHED":
            raise PlatformLimitReached
        if reason is not None or model is None:
            return None
        api_key = platform_api_key(self.settings, model.provider)
        if not api_key:
            return None
        return ResolvedModel(
            source="PLATFORM",
            config=ProviderConfig(provider=Provider(model.provider), model=model.model, api_key=api_key),
            platform_model_id=model.id,
            input_cents_per_mtok=charge_rate(model, model.input_cost_per_mtok_cents),
            output_cents_per_mtok=charge_rate(model, model.output_cost_per_mtok_cents),
        )

    # Owners

    def owner_view(self, account_id: UUID) -> dict[str, object]:
        access = self.db.get(AiPlatformAccess, account_id)
        model = self.db.get(AiPlatformModel, access.platform_model_id) if access and access.platform_model_id else None
        return {
            "status": access.status if access else None,
            "model_label": model.label if model else None,
            "requested_at": access.requested_at if access else None,
            "usage": platform_usage(self.db, self.settings, access).as_dict()
            if access and access.status == AiPlatformAccessStatus.ENABLED.value
            else None,
            "currency": self.settings.billing_currency,
        }

    def request(self, *, account_id: UUID, current_user: User, accept_terms: bool) -> None:
        self.require_owner(account_id, current_user)
        if not accept_terms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Accept that usage past the free allowance is added to your bill.",
            )
        access = self.db.get(AiPlatformAccess, account_id)
        if access is not None and access.status == AiPlatformAccessStatus.ENABLED.value:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The platform model is already on for this account.")
        if access is None:
            access = AiPlatformAccess(
                account_id=account_id,
                status=AiPlatformAccessStatus.REQUESTED.value,
                allowance_kind=self.settings.ai_default_allowance_kind,
                allowance_amount=self.settings.ai_default_allowance_amount,
            )
            self.db.add(access)
        access.status = AiPlatformAccessStatus.REQUESTED.value
        access.requested_by = current_user.id
        access.requested_at = datetime.now(UTC)
        self.record_for_account(account_id, current_user, "requested")
        self.db.commit()

    def cancel(self, *, account_id: UUID, current_user: User) -> None:
        """The owner withdraws the request, or stops using the platform model."""
        self.require_owner(account_id, current_user)
        access = self.db.get(AiPlatformAccess, account_id)
        if access is None or access.status not in (AiPlatformAccessStatus.REQUESTED.value, AiPlatformAccessStatus.ENABLED.value):
            return
        access.status = AiPlatformAccessStatus.DISABLED.value
        # Switching it back on needs a fresh request.
        access.requested_at = None
        self.record_for_account(account_id, current_user, "turned off by an owner")
        self.db.commit()

    # Platform admins: models

    def list_models(self) -> list[dict[str, object]]:
        models = self.db.scalars(select(AiPlatformModel).order_by(AiPlatformModel.is_active.desc(), AiPlatformModel.label)).all()
        return [self.model_dict(model) for model in models]

    def create_model(self, *, actor: User, model_in: AiPlatformModelCreate) -> dict[str, object]:
        model = AiPlatformModel(
            label=model_in.label.strip(),
            provider=model_in.provider,
            model=model_in.model.strip(),
            input_cost_per_mtok_cents=Decimal(str(model_in.input_cost_per_mtok_cents)),
            output_cost_per_mtok_cents=Decimal(str(model_in.output_cost_per_mtok_cents)),
            margin_percent=Decimal(str(model_in.margin_percent)),
            is_active=model_in.is_active,
        )
        self.db.add(model)
        self.db.flush()
        record_platform_action(
            self.db, actor=actor, action=PlatformAiAction.MODEL_CREATED, target=model.label,
            details={"provider": model.provider, "model": model.model, "margin_percent": float(model.margin_percent)},
        )
        self.db.commit()
        return self.model_dict(model)

    def update_model(self, *, actor: User, model_id: UUID, changes: AiPlatformModelUpdate) -> dict[str, object]:
        model = self.db.get(AiPlatformModel, model_id)
        if model is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
        values = changes.model_dump(exclude_unset=True, exclude_none=True)
        for field, value in values.items():
            if isinstance(value, float):
                value = Decimal(str(value))
            setattr(model, field, value.strip() if isinstance(value, str) else value)
        record_platform_action(
            self.db, actor=actor, action=PlatformAiAction.MODEL_UPDATED, target=model.label,
            details={k: (float(v) if isinstance(v, Decimal) else v) for k, v in values.items()},
        )
        self.db.commit()
        return self.model_dict(model)

    def model_dict(self, model: AiPlatformModel) -> dict[str, object]:
        return {
            "id": model.id,
            "label": model.label,
            "provider": model.provider,
            "model": model.model,
            "input_cost_per_mtok_cents": float(model.input_cost_per_mtok_cents),
            "output_cost_per_mtok_cents": float(model.output_cost_per_mtok_cents),
            "margin_percent": float(model.margin_percent),
            "is_active": model.is_active,
            "key_configured": bool(_env_key(self.settings, model.provider) or self.settings.ai_platform_key_param_prefix),
        }

    # Platform admins: accounts

    def list_accounts(self) -> list[dict[str, object]]:
        rows = self.db.execute(
            select(AiPlatformAccess, Account).join(Account, Account.id == AiPlatformAccess.account_id).order_by(Account.name)
        ).all()
        return [self.account_dict(access, account) for access, account in rows]

    def decide(self, *, actor: User, account_id: UUID, decision: AiPlatformAccessUpdate) -> dict[str, object]:
        account = self.db.get(Account, account_id)
        access = self.db.get(AiPlatformAccess, account_id)
        if account is None or access is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This account hasn't asked for the platform model.")
        if decision.allowance_kind is not None:
            access.allowance_kind = AllowanceKind(decision.allowance_kind).value
        if decision.allowance_amount is not None:
            access.allowance_amount = decision.allowance_amount
        if decision.clear_cap:
            access.monthly_cap_cents = None
        elif decision.monthly_cap_cents is not None:
            access.monthly_cap_cents = decision.monthly_cap_cents
        if decision.platform_model_id is not None:
            model = self.db.get(AiPlatformModel, decision.platform_model_id)
            if model is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick one of the platform models.")
            access.platform_model_id = model.id

        previous = access.status
        if decision.status == AiPlatformAccessStatus.ENABLED.value:
            # The owner has to ask first: it adds to their bill.
            if previous != AiPlatformAccessStatus.ENABLED.value and access.requested_at is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account owner has to request the platform model before it can be switched on.",
                )
            model = self.db.get(AiPlatformModel, access.platform_model_id) if access.platform_model_id else None
            if model is None or not model.is_active:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick an active platform model.")
        access.status = decision.status
        if previous != decision.status:
            access.decided_by = actor.id
            access.decided_at = datetime.now(UTC)
        action = {
            ("ENABLED", True): PlatformAiAction.ENABLED,
            ("DISABLED", True): PlatformAiAction.DISABLED,
            ("DECLINED", True): PlatformAiAction.DECLINED,
        }.get((decision.status, previous != decision.status), PlatformAiAction.UPDATED)
        record_platform_action(
            self.db, actor=actor, action=action, account=account,
            details={
                "status": access.status,
                "allowance_kind": access.allowance_kind,
                "allowance_amount": access.allowance_amount,
                "monthly_cap_cents": access.monthly_cap_cents,
            },
        )
        self.db.commit()
        return self.account_dict(access, account)

    def account_dict(self, access: AiPlatformAccess, account: Account) -> dict[str, object]:
        model = self.db.get(AiPlatformModel, access.platform_model_id) if access.platform_model_id else None
        requested_by = self.db.get(User, access.requested_by) if access.requested_by else None
        own = self.db.get(AiAccountSettings, access.account_id)
        usage: MonthUsage = platform_usage(self.db, self.settings, access)
        return {
            "account_id": access.account_id,
            "account_name": account.name,
            "status": access.status,
            "platform_model_id": access.platform_model_id,
            "model_label": model.label if model else None,
            "allowance_kind": access.allowance_kind,
            "allowance_amount": access.allowance_amount,
            "monthly_cap_cents": access.monthly_cap_cents,
            "has_own_key": bool(own and own.has_key),
            "requested_at": access.requested_at,
            "requested_by_email": requested_by.email if requested_by else None,
            "decided_at": access.decided_at,
            "usage": usage.as_dict(),
        }

    def defaults(self) -> dict[str, Any]:
        return {
            "allowance_kind": self.settings.ai_default_allowance_kind,
            "allowance_amount": self.settings.ai_default_allowance_amount,
            "currency": self.settings.billing_currency,
        }

    # Helpers

    def require_owner(self, account_id: UUID, current_user: User) -> None:
        if not access_for(self.db, account_id=account_id, user=current_user).is_owner:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only account owners manage the AI assistant.")

    def record_for_account(self, account_id: UUID, user: User, what: str) -> None:
        ActivityLogService(self.db).record(
            account_id=account_id,
            entity_type="ACCOUNT",
            entity_id=account_id,
            action="UPDATED",
            new_values={"ai_platform_model": what},
            created_by=user.id,
        )
