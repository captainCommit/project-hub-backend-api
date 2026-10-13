"""People's conversations with the assistant: listing, sending a question, and what the chat shows.

A conversation belongs to one person in one account; nobody else can open it, owners included. Sending a
question stores it, creates a QUEUED run and returns at once; the AI worker answers it (app.services.ai.runner)
and the chat polls the conversation until the run is finished.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.ai import AiAccountSettings, AiConversation, AiMessage, AiReport, AiRun, AiRunStatus, DEFAULT_RETENTION_DAYS
from app.models.user import User
from app.services.access import access_for
from app.services.ai.account_settings import AiSettingsService
from app.services.ai.tools import progress_label

MAX_QUESTION_CHARS = 4000
CONVERSATION_LIST_LIMIT = 50
TITLE_CHARS = 60
UNAVAILABLE_MESSAGES = {
    "OFF": "An account owner has switched the assistant off.",
    "NOT_SET_UP": "The assistant isn't set up for this account yet.",
    "LIMIT_REACHED": "This account has used its AI allowance for the month.",
}
STALE_RUN_ERROR = "That took too long to answer. Try again."


def stale_after(settings: Settings) -> timedelta:
    """A run still unfinished this long after it was created has died (the worker's own limit is shorter)."""
    return timedelta(seconds=settings.ai_run_timeout_seconds + 60)


def require_ai_available(db: Session, settings: Settings, *, account_id: UUID, current_user: User) -> None:
    availability = AiSettingsService(db, settings).status(account_id=account_id, current_user=current_user)
    if not availability["available"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=UNAVAILABLE_MESSAGES.get(str(availability["reason"]), UNAVAILABLE_MESSAGES["NOT_SET_UP"]),
        )


def require_under_hourly_limit(db: Session, settings: Settings, *, current_user: User) -> None:
    """Questions and reports share one hourly limit per person."""
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    asked = db.scalar(select(func.count()).select_from(AiRun).where(AiRun.user_id == current_user.id, AiRun.created_at >= hour_ago))
    if (asked or 0) >= settings.ai_questions_per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You've asked the assistant for {asked} answers or reports in the last hour, the most allowed. Try again a little later.",
        )


class ConversationService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def list(self, *, account_id: UUID, current_user: User) -> list[AiConversation]:
        access_for(self.db, account_id=account_id, user=current_user)
        return list(
            self.db.scalars(
                select(AiConversation)
                .where(AiConversation.account_id == account_id, AiConversation.user_id == current_user.id)
                .order_by(AiConversation.updated_at.desc(), AiConversation.id)
                .limit(CONVERSATION_LIST_LIMIT)
            ).all()
        )

    def create(self, *, account_id: UUID, current_user: User, title: str | None) -> AiConversation:
        access_for(self.db, account_id=account_id, user=current_user)
        conversation = AiConversation(
            account_id=account_id, user_id=current_user.id, title=(title or "").strip()[:200] or None
        )
        self.db.add(conversation)
        self.db.commit()
        return conversation

    def get(self, *, conversation_id: UUID, current_user: User) -> AiConversation:
        conversation = self.db.get(AiConversation, conversation_id)
        # Someone else's conversation looks the same as one that doesn't exist.
        if conversation is None or conversation.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
        access_for(self.db, account_id=conversation.account_id, user=current_user)
        return conversation

    def delete(self, *, conversation_id: UUID, current_user: User) -> None:
        conversation = self.get(conversation_id=conversation_id, current_user=current_user)
        self.db.delete(conversation)
        self.db.commit()

    def send(self, *, conversation_id: UUID, current_user: User, text: str) -> AiRun:
        conversation = self.get(conversation_id=conversation_id, current_user=current_user)
        text = text.strip()
        if not text:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Type a question first.")
        if len(text) > MAX_QUESTION_CHARS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Keep questions under {MAX_QUESTION_CHARS} characters."
            )
        require_ai_available(self.db, self.settings, account_id=conversation.account_id, current_user=current_user)
        self.fail_stale_runs(conversation_id=conversation.id)
        if self.active_run(conversation.id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Wait for the current answer to finish.")
        require_under_hourly_limit(self.db, self.settings, current_user=current_user)

        run = AiRun(conversation_id=conversation.id, account_id=conversation.account_id, user_id=current_user.id)
        self.db.add(run)
        self.db.flush()
        position = self.db.scalar(
            select(func.coalesce(func.max(AiMessage.position), -1)).where(AiMessage.conversation_id == conversation.id)
        )
        self.db.add(
            AiMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                position=int(position) + 1,
                role="user",
                content={"role": "user", "content": text},
            )
        )
        if not conversation.title:
            conversation.title = text[:TITLE_CHARS] + ("…" if len(text) > TITLE_CHARS else "")
        conversation.updated_at = datetime.now(UTC)
        self.db.commit()
        return run

    def active_run(self, conversation_id: UUID) -> AiRun | None:
        return self.db.scalar(
            select(AiRun).where(
                AiRun.conversation_id == conversation_id,
                AiRun.status.in_([AiRunStatus.QUEUED.value, AiRunStatus.RUNNING.value]),
            )
        )

    def fail_stale_runs(
        self, *, conversation_id: UUID | None = None, run_id: UUID | None = None, now: datetime | None = None
    ) -> int:
        """Runs whose worker died (timed out or never started) would otherwise show as 'thinking' forever."""
        now = now or datetime.now(UTC)
        statement = update(AiRun).where(
            AiRun.status.in_([AiRunStatus.QUEUED.value, AiRunStatus.RUNNING.value]),
            AiRun.created_at < now - stale_after(self.settings),
        )
        if conversation_id is not None:
            statement = statement.where(AiRun.conversation_id == conversation_id)
        if run_id is not None:
            statement = statement.where(AiRun.id == run_id)
        result = self.db.execute(
            statement.values(
                status=AiRunStatus.FAILED.value, error_kind="TIMEOUT", error=STALE_RUN_ERROR, progress=None, finished_at=now
            )
        )
        self.db.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    # What the chat shows

    def present(self, conversation: AiConversation) -> dict[str, Any]:
        """The conversation as the chat shows it: each question, then one answer with the lookups it made."""
        self.fail_stale_runs(conversation_id=conversation.id)
        rows = self.db.scalars(
            select(AiMessage).where(AiMessage.conversation_id == conversation.id).order_by(AiMessage.position)
        ).all()
        run_ids = {row.run_id for row in rows if row.run_id}
        runs = {run.id: run for run in self.db.scalars(select(AiRun).where(AiRun.id.in_(run_ids))).all()} if run_ids else {}
        by_run: dict[UUID | None, list[AiMessage]] = {}
        for row in rows:
            if row.role != "user":
                by_run.setdefault(row.run_id, []).append(row)

        messages: list[dict[str, Any]] = []
        active = None
        for row in rows:
            if row.role != "user":
                continue
            messages.append({"id": str(row.id), "role": "user", "content": row.content.get("content", ""), "created_at": row.created_at})
            run = runs.get(row.run_id) if row.run_id else None
            if run is None:
                continue
            steps, answer = [], ""
            for part in by_run.get(run.id, []):
                if part.role == "assistant":
                    for call in part.content.get("tool_calls") or []:
                        steps.append(progress_label(call.get("name", ""), call.get("arguments") or {}).rstrip("…"))
                    if not part.content.get("tool_calls"):
                        answer = part.content.get("content") or ""
            if run.is_active:
                active = run
            messages.append(
                {
                    "id": str(run.id),
                    "role": "assistant",
                    "status": run.status,
                    "progress": run.progress if run.is_active else None,
                    "steps": steps,
                    "content": answer,
                    "error": run.error if run.status == AiRunStatus.FAILED.value else None,
                    "created_at": run.finished_at or run.created_at,
                }
            )
        return {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "messages": messages,
            "active_run_id": active.id if active else None,
        }

    # Housekeeping (hourly)

    def delete_expired(self, *, now: datetime | None = None) -> int:
        """Delete conversations untouched, and reports written, longer ago than their account keeps them."""
        now = now or datetime.now(UTC)
        retention = dict(self.db.execute(select(AiAccountSettings.account_id, AiAccountSettings.retention_days)).all())
        account_ids = set(self.db.scalars(select(AiConversation.account_id).distinct()).all())
        account_ids |= set(self.db.scalars(select(AiReport.account_id).distinct()).all())
        deleted = 0
        for account_id in account_ids:
            cutoff = now - timedelta(days=retention.get(account_id, DEFAULT_RETENTION_DAYS))
            for statement in (
                delete(AiConversation).where(AiConversation.account_id == account_id, AiConversation.updated_at < cutoff),
                delete(AiReport).where(AiReport.account_id == account_id, AiReport.created_at < cutoff),
            ):
                deleted += int(self.db.execute(statement).rowcount or 0)  # type: ignore[attr-defined]
        self.db.commit()
        return deleted
