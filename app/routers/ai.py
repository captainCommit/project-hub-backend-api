from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.ai import AiConversation
from app.models.user import User
from app.schemas.ai import (
    AiConversationCreate,
    AiConversationDetail,
    AiConversationRead,
    AiKeyUpdate,
    AiQuestionCreate,
    AiSettingsRead,
    AiSettingsUpdate,
    AiStatusRead,
)
from app.services.ai.account_settings import AiSettingsService
from app.services.ai.conversations import ConversationService
from app.services.ai.dispatch import DispatchFailed, Dispatcher, dispatch_run
from app.services.ai.endpoints import Resolver, resolve_host
from app.services.ai.providers import Transport, requests_transport
from app.services.ai.runner import AssistantRunner
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


# Conversations ------------------------------------------------------------------------------------------


# Overridden in tests to answer with the test database and a fake model.
def get_ai_dispatcher(settings: Settings = Depends(get_settings)) -> Dispatcher:
    return lambda run_id, background: dispatch_run(run_id, background, settings)


def conversation_service(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ConversationService:
    return ConversationService(db, settings)


@router.get("/accounts/{account_id}/ai/conversations", response_model=list[AiConversationRead], summary="Your conversations, newest first")
def list_ai_conversations(
    account_id: UUID,
    service: ConversationService = Depends(conversation_service),
    current_user: User = Depends(get_current_user),
) -> list[AiConversation]:
    return service.list(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/ai/conversations",
    response_model=AiConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
)
def create_ai_conversation(
    account_id: UUID,
    conversation_in: AiConversationCreate,
    service: ConversationService = Depends(conversation_service),
    current_user: User = Depends(get_current_user),
) -> AiConversation:
    return service.create(account_id=account_id, current_user=current_user, title=conversation_in.title)


@router.get("/ai/conversations/{conversation_id}", response_model=AiConversationDetail, summary="A conversation and its answers (polled while one is being written)")
def get_ai_conversation(
    conversation_id: UUID,
    service: ConversationService = Depends(conversation_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return service.present(service.get(conversation_id=conversation_id, current_user=current_user))


@router.delete("/ai/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a conversation")
def delete_ai_conversation(
    conversation_id: UUID,
    service: ConversationService = Depends(conversation_service),
    current_user: User = Depends(get_current_user),
) -> Response:
    service.delete(conversation_id=conversation_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/ai/conversations/{conversation_id}/messages",
    response_model=AiConversationDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask a question; the answer is written in the background",
)
def ask_ai(
    conversation_id: UUID,
    question: AiQuestionCreate,
    background: BackgroundTasks,
    service: ConversationService = Depends(conversation_service),
    dispatcher: Dispatcher = Depends(get_ai_dispatcher),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    run = service.send(conversation_id=conversation_id, current_user=current_user, text=question.content)
    try:
        dispatcher(run.id, background)
    except DispatchFailed as exc:
        AssistantRunner(service.db, service.settings).fail(run, "FAILED", "The assistant couldn't start. Try again.")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The assistant couldn't start. Try again.") from exc
    return service.present(service.get(conversation_id=conversation_id, current_user=current_user))
