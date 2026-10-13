"""Owners' assistant settings: switching it on or off, chat retention, and the account's own model key."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.account import Account
from app.models.ai import AiAccountSettings
from app.models.user import User
from app.schemas.ai import AiKeyUpdate, AiSettingsUpdate
from app.services.access import access_for
from app.services.activity import ActivityLogService
from app.services.ai.endpoints import Resolver, UnsafeEndpoint, check_endpoint, resolve_host
from app.services.ai.keys import KeyCipher, KeyStorageUnavailable, key_cipher, last4
from app.services.ai.providers import (
    PROVIDER_LABELS,
    PROVIDERS_WITH_ENDPOINT,
    Provider,
    ProviderConfig,
    ProviderError,
    Transport,
    check_connection,
    requests_transport,
)

logger = logging.getLogger(__name__)

KEY_STORAGE_UNAVAILABLE = "This server isn't set up to store model keys yet. Ask your Project Hub administrator."


@dataclass(frozen=True)
class ResolvedModel:
    """The model a question will be answered with."""

    source: Literal["BYOK", "PLATFORM"]
    config: ProviderConfig
    platform_model_id: UUID | None = None


class AiSettingsService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        *,
        transport: Transport = requests_transport,
        resolver: Resolver = resolve_host,
    ) -> None:
        self.db = db
        self.settings = settings
        self.transport = transport
        self.resolver = resolver

    # Everyone in the account

    def status(self, *, account_id: UUID, current_user: User) -> dict[str, object]:
        scope = access_for(self.db, account_id=account_id, user=current_user)
        row = self.db.get(AiAccountSettings, account_id)
        result: dict[str, object] = {
            "available": False,
            "source": None,
            "reason": None,
            "provider_label": None,
            "model": None,
            "can_manage": scope.is_owner,
        }
        if row is not None and not row.assistant_enabled:
            return {**result, "reason": "OFF"}
        if row is not None and row.has_key:
            assert row.provider is not None
            return {
                **result,
                "available": True,
                "source": "BYOK",
                "provider_label": PROVIDER_LABELS[Provider(row.provider)],
                "model": row.model,
            }
        return {**result, "reason": "NOT_SET_UP"}

    # Owners

    def read(self, *, account_id: UUID, current_user: User) -> dict[str, object]:
        self.require_owner(account_id, current_user)
        row = self.db.get(AiAccountSettings, account_id) or AiAccountSettings(
            account_id=account_id, assistant_enabled=True, retention_days=90
        )
        key = None
        if row.has_key:
            assert row.provider is not None and row.model is not None
            updated_by = self.db.get(User, row.key_updated_by) if row.key_updated_by else None
            key = {
                "provider": row.provider,
                "provider_label": PROVIDER_LABELS[Provider(row.provider)],
                "model": row.model,
                "base_url": row.base_url,
                "azure_api_version": row.azure_api_version,
                "key_last4": row.key_last4,
                "key_updated_at": row.key_updated_at,
                "key_updated_by_name": (updated_by.full_name or updated_by.email) if updated_by else None,
            }
        return {
            "assistant_enabled": row.assistant_enabled,
            "retention_days": row.retention_days,
            "key": key,
            "key_storage_available": self.key_storage_available(),
        }

    def update(self, *, account_id: UUID, current_user: User, changes: AiSettingsUpdate) -> dict[str, object]:
        self.require_owner(account_id, current_user)
        row = self.get_or_create(account_id)
        values = changes.model_dump(exclude_unset=True, exclude_none=True)
        old = {field: getattr(row, field) for field in values}
        for field, value in values.items():
            setattr(row, field, value)
        changed = {field: value for field, value in values.items() if old[field] != value}
        if changed:
            self.record(account_id, current_user, old={f: old[f] for f in changed}, new=changed)
        self.db.commit()
        return self.read(account_id=account_id, current_user=current_user)

    def save_key(self, *, account_id: UUID, current_user: User, key_in: AiKeyUpdate) -> dict[str, object]:
        """Check the key with a real (tiny) request, then store it encrypted. Nothing is saved if the check fails."""
        self.require_owner(account_id, current_user)
        if not key_in.accept_data_notice:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Confirm that questions send project data to the provider you chose.",
            )
        provider = Provider(key_in.provider)
        api_key = key_in.api_key.strip()
        model = key_in.model.strip()
        base_url = None
        if provider in PROVIDERS_WITH_ENDPOINT:
            try:
                base_url = check_endpoint(key_in.base_url or "", resolver=self.resolver)
            except UnsafeEndpoint as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if not api_key and provider != Provider.CUSTOM:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enter the API key.")
        try:
            cipher = self.cipher()
        except KeyStorageUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=KEY_STORAGE_UNAVAILABLE) from exc

        config = ProviderConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            azure_api_version=(key_in.azure_api_version or "").strip() or None,
        )
        try:
            check_connection(config, transport=self.transport, resolver=self.resolver)
        except ProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"The key didn't work: {exc.message}"
            ) from exc

        row = self.get_or_create(account_id)
        previous = self.describe_key(row)
        row.provider = provider.value
        row.model = model
        row.base_url = base_url
        row.azure_api_version = config.azure_api_version if provider == Provider.AZURE_OPENAI else None
        row.encrypted_key = cipher.encrypt(account_id, api_key)
        row.key_last4 = last4(api_key) or None
        row.key_updated_by = current_user.id
        row.key_updated_at = datetime.now(UTC)
        row.data_notice_accepted_by = current_user.id
        row.data_notice_accepted_at = row.key_updated_at
        self.record(account_id, current_user, old={"ai_model": previous}, new={"ai_model": self.describe_key(row)})
        self.db.commit()
        return self.read(account_id=account_id, current_user=current_user)

    def remove_key(self, *, account_id: UUID, current_user: User) -> dict[str, object]:
        self.require_owner(account_id, current_user)
        row = self.db.get(AiAccountSettings, account_id)
        if row is not None and row.has_key:
            previous = self.describe_key(row)
            row.provider = row.model = row.base_url = row.azure_api_version = row.key_last4 = None
            row.encrypted_key = None
            row.key_updated_by = current_user.id
            row.key_updated_at = datetime.now(UTC)
            self.record(account_id, current_user, old={"ai_model": previous}, new={"ai_model": None})
            self.db.commit()
        return self.read(account_id=account_id, current_user=current_user)

    # The worker

    def resolve_byok(self, account_id: UUID) -> ResolvedModel | None:
        """The account's own model, with its key decrypted. Only the AI worker can decrypt, deployed."""
        row = self.db.get(AiAccountSettings, account_id)
        if row is None or not row.assistant_enabled or not row.has_key:
            return None
        assert row.provider is not None and row.model is not None and row.encrypted_key is not None
        return ResolvedModel(
            source="BYOK",
            config=ProviderConfig(
                provider=Provider(row.provider),
                model=row.model,
                api_key=self.cipher().decrypt(account_id, row.encrypted_key),
                base_url=row.base_url,
                azure_api_version=row.azure_api_version,
            ),
        )

    # Helpers

    def cipher(self) -> KeyCipher:
        return key_cipher(self.settings)

    def key_storage_available(self) -> bool:
        return bool(self.settings.ai_kms_key_id) or (
            self.settings.environment == "local" and bool(self.settings.ai_local_key_secret)
        )

    def get_or_create(self, account_id: UUID) -> AiAccountSettings:
        row = self.db.get(AiAccountSettings, account_id)
        if row is None:
            if self.db.get(Account, account_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
            row = AiAccountSettings(account_id=account_id, assistant_enabled=True, retention_days=90)
            self.db.add(row)
            self.db.flush()
        return row

    def require_owner(self, account_id: UUID, current_user: User) -> None:
        if not access_for(self.db, account_id=account_id, user=current_user).is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only account owners manage the AI assistant."
            )

    @staticmethod
    def describe_key(row: AiAccountSettings) -> str | None:
        """For the account's history: provider, model and the key's last characters, never the key."""
        if not row.has_key:
            return None
        assert row.provider is not None
        ending = f", key ending {row.key_last4}" if row.key_last4 else ""
        return f"{PROVIDER_LABELS[Provider(row.provider)]} · {row.model}{ending}"

    def record(self, account_id: UUID, user: User, *, old: dict[str, object], new: dict[str, object]) -> None:
        ActivityLogService(self.db).record(
            account_id=account_id,
            entity_type="ACCOUNT",
            entity_id=account_id,
            action="UPDATED",
            old_values={f"ai_{k}" if not k.startswith("ai_") else k: v for k, v in old.items()},
            new_values={f"ai_{k}" if not k.startswith("ai_") else k: v for k, v in new.items()},
            created_by=user.id,
        )
