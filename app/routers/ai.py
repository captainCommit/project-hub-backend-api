from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.ai import AiKeyUpdate, AiSettingsRead, AiSettingsUpdate, AiStatusRead
from app.services.ai.account_settings import AiSettingsService
from app.services.ai.endpoints import Resolver, resolve_host
from app.services.ai.providers import Transport, requests_transport
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["ai"])


# Overridden in tests so no request leaves the machine.
def get_ai_transport() -> Transport:
    return requests_transport


def get_ai_resolver() -> Resolver:
    return resolve_host


def settings_service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    transport: Transport = Depends(get_ai_transport),
    resolver: Resolver = Depends(get_ai_resolver),
) -> AiSettingsService:
    return AiSettingsService(db, settings, transport=transport, resolver=resolver)


@router.get("/accounts/{account_id}/ai/status", response_model=AiStatusRead, summary="Whether the assistant can be used")
def get_ai_status(
    account_id: UUID,
    service: AiSettingsService = Depends(settings_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return service.status(account_id=account_id, current_user=current_user)


@router.get("/accounts/{account_id}/ai/settings", response_model=AiSettingsRead, summary="Assistant settings (owners)")
def get_ai_settings(
    account_id: UUID,
    service: AiSettingsService = Depends(settings_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return service.read(account_id=account_id, current_user=current_user)


@router.patch("/accounts/{account_id}/ai/settings", response_model=AiSettingsRead, summary="Turn the assistant on or off, set chat retention (owners)")
def update_ai_settings(
    account_id: UUID,
    changes: AiSettingsUpdate,
    service: AiSettingsService = Depends(settings_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return service.update(account_id=account_id, current_user=current_user, changes=changes)


@router.put("/accounts/{account_id}/ai/key", response_model=AiSettingsRead, summary="Check and save the account's own model key (owners)")
def save_ai_key(
    account_id: UUID,
    key_in: AiKeyUpdate,
    service: AiSettingsService = Depends(settings_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return service.save_key(account_id=account_id, current_user=current_user, key_in=key_in)


@router.delete("/accounts/{account_id}/ai/key", response_model=AiSettingsRead, summary="Remove the account's own model key (owners)")
def remove_ai_key(
    account_id: UUID,
    service: AiSettingsService = Depends(settings_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return service.remove_key(account_id=account_id, current_user=current_user)
