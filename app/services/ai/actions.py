"""Work Compass can do for the person: drafting a risk, issue or task that they review and then create.

The model never creates anything itself. Its draft_record tool only checks and shapes a draft; the chat shows it
as a review card, and the record is created when the person presses Create (confirm_draft), as that person,
through the same service and permission checks as the RAID log or the sheet.
"""

import json
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.access_grant import ProjectModule
from app.models.ai import AiConversation, AiMessage
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.program import Program
from app.models.project import Project
from app.models.user import User
from app.schemas.raid import IssueCreate, RiskCreate
from app.schemas.tasks import TaskCreate
from app.services.access import access_for

DRAFT_TOOL = "draft_record"

KINDS: dict[str, dict[str, Any]] = {
    "risk": {"entity": "RISK", "module": ProjectModule.RISKS, "label": "Risk", "default_status": "OPEN"},
    "issue": {"entity": "ISSUE", "module": ProjectModule.ISSUES, "label": "Issue", "default_status": "OPEN"},
    "task": {"entity": "TASK", "module": ProjectModule.TASKS, "label": "Task", "default_status": "NOT_STARTED"},
}
PRIORITY_DEFAULT = "MEDIUM"
TEXT_FIELDS = ("title", "cause", "effect", "description", "assigned_to_name")
MAX_TEXT = {"title": 255, "assigned_to_name": 255}


class DraftError(ValueError):
    """Something in a draft can't be used; the message says what, for the model or the person."""


def option_key(value: str) -> str:
    return "_".join("".join(ch if ch.isalnum() else " " for ch in value).split()).upper()


def find_option(db: Session, *, account_id: UUID, entity: str, name: str, wanted: str) -> OptionValue | None:
    """An active option by its key or label ("medium", "Medium" and "MEDIUM" all match)."""
    key = option_key(wanted)
    values = db.scalars(
        select(OptionValue)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == account_id,
            OptionSet.entity_type == entity,
            OptionSet.name == name,
            OptionValue.is_active.is_(True),
        )
    ).all()
    for value in values:
        if value.value.upper() == key or option_key(value.label) == key:
            return value
    return None


def option_labels(db: Session, *, account_id: UUID, entity: str, name: str) -> list[str]:
    return list(
        db.scalars(
            select(OptionValue.label)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionSet.account_id == account_id,
                OptionSet.entity_type == entity,
                OptionSet.name == name,
                OptionValue.is_active.is_(True),
            )
            .order_by(OptionValue.sort_order)
        ).all()
    )


def build_draft(db: Session, *, account_id: UUID, user: User, fields: dict[str, Any], need_project: bool) -> dict[str, Any]:
    """A checked draft: {kind, project_id, project, program, title, ..., priority, priority_label, status, status_label}.

    The project may be left empty while drafting (the card asks for it); creating needs one.
    """
    kind = str(fields.get("kind") or "").lower()
    if kind not in KINDS:
        raise DraftError(f"kind must be one of: {', '.join(KINDS)}.")
    config = KINDS[kind]
    draft: dict[str, Any] = {"kind": kind}
    for name in TEXT_FIELDS:
        value = fields.get(name)
        if isinstance(value, str) and value.strip():
            draft[name] = value.strip()[: MAX_TEXT.get(name, 4000)]
    if kind == "risk":
        draft.pop("description", None)
    else:
        draft.pop("cause", None)
        draft.pop("effect", None)
    if not draft.get("title"):
        raise DraftError("A title is needed.")

    project_id = fields.get("project_id")
    if project_id:
        try:
            project = db.get(Project, UUID(str(project_id)))
        except ValueError as exc:
            raise DraftError("project_id isn't a valid id; find the project with search or list_projects.") from exc
        if project is None or project.account_id != account_id:
            raise DraftError("That project wasn't found.")
        scope = access_for(db, account_id=account_id, user=user)
        try:
            # Viewing the project and creating in this part of it, as the person.
            scope.require_project(project, module=config["module"], action="CREATE")
        except HTTPException as exc:
            if exc.status_code == status.HTTP_402_PAYMENT_REQUIRED:
                raise DraftError(str(exc.detail)) from exc
            raise DraftError(f"You can't add a {kind} to that project.") from exc
        program = db.get(Program, project.program_id)
        draft.update(project_id=str(project.id), project=project.name, program=program.name if program else None)
    elif need_project:
        raise DraftError("Choose the project to add it to.")

    for name, default in (("priority", PRIORITY_DEFAULT), ("status", config["default_status"])):
        wanted = str(fields.get(name) or default)
        option = find_option(db, account_id=account_id, entity=config["entity"], name=name.upper(), wanted=wanted)
        if option is None and fields.get(name):
            choices = option_labels(db, account_id=account_id, entity=config["entity"], name=name.upper())
            raise DraftError(f"Unknown {name} \"{wanted}\". Use one of: {', '.join(choices)}.")
        if option is not None:
            draft[name] = option.value
            draft[f"{name}_label"] = option.label

    target = fields.get("target_date")
    if target:
        try:
            draft["target_date"] = date.fromisoformat(str(target)[:10]).isoformat()
        except ValueError as exc:
            raise DraftError("target_date must be a date like 2026-12-31.") from exc
    return draft


def draft_from_tool_result(content: Any) -> dict[str, Any] | None:
    """The draft inside a draft_record tool result, if it made one."""
    try:
        data = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return None
    draft = data.get("draft") if isinstance(data, dict) else None
    return draft if isinstance(draft, dict) else None


def create_from_draft(db: Session, *, user: User, draft: dict[str, Any]) -> dict[str, Any]:
    """Create the record as the person; returns {kind, id, number, title, link, project}."""
    from app.services.ai.tools import record_link, task_link
    from app.services.raid import RaidService
    from app.services.tasks import TaskService

    kind = draft["kind"]
    project_id = UUID(draft["project_id"])
    target = date.fromisoformat(draft["target_date"]) if draft.get("target_date") else None
    if kind == "risk":
        created = RaidService(db).create_risk(
            project_id=project_id,
            risk_in=RiskCreate(
                title=draft["title"], cause=draft.get("cause"), effect=draft.get("effect"),
                priority=draft.get("priority"), status=draft.get("status"),
                assigned_to_name=draft.get("assigned_to_name"), target_resolution_date=target,
            ),
            current_user=user,
        )
        number, entity = created.get("risk_number"), "RISK"
    elif kind == "issue":
        created = RaidService(db).create_issue(
            project_id=project_id,
            issue_in=IssueCreate(
                title=draft["title"], description=draft.get("description"),
                priority=draft.get("priority"), status=draft.get("status"),
                assigned_to_name=draft.get("assigned_to_name"), target_resolution_date=target,
            ),
            current_user=user,
        )
        number, entity = created.get("issue_number"), "ISSUE"
    else:
        project = db.get(Project, project_id)
        option_ids = {}
        for name in ("status", "priority"):
            if draft.get(name):
                option = find_option(db, account_id=project.account_id, entity="TASK", name=name.upper(), wanted=draft[name])
                option_ids[f"{name}_id"] = option.id if option else None
        created = TaskService(db).create_task(
            project_id=project_id,
            task_in=TaskCreate(name=draft["title"], description=draft.get("description"), finish_date=target, **option_ids),
            current_user=user,
        )
        number, entity = None, "TASK"
    record_id = created["id"] if isinstance(created, dict) else created.id
    link = task_link(project_id, record_id) if entity == "TASK" else record_link(entity, record_id)
    return {
        "kind": kind,
        "id": str(record_id),
        "number": number,
        "title": draft["title"],
        "link": link,
        "project": draft.get("project"),
    }


def get_draft_message(db: Session, *, message_id: UUID, user: User) -> tuple[AiMessage, AiConversation, dict[str, Any]]:
    """A draft the person made in one of their own conversations; anything else looks like it doesn't exist."""
    message = db.get(AiMessage, message_id)
    conversation = db.get(AiConversation, message.conversation_id) if message else None
    draft = draft_from_tool_result(message.content.get("content")) if message and message.role == "tool" else None
    if conversation is None or conversation.user_id != user.id or draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found.")
    return message, conversation, draft


def confirm_draft(db: Session, *, message_id: UUID, user: User, changes: dict[str, Any]) -> dict[str, Any]:
    """Create the drafted record, with the person's edits from the review card, and remember that it was created."""
    message, conversation, draft = get_draft_message(db, message_id=message_id, user=user)
    if message.outcome and message.outcome.get("status") == "CREATED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This has already been created.")
    fields = {**draft, **{key: value for key, value in changes.items() if key != "kind"}}
    try:
        final = build_draft(db, account_id=conversation.account_id, user=user, fields=fields, need_project=True)
    except DraftError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    created = create_from_draft(db, user=user, draft=final)
    message.outcome = {"status": "CREATED", "draft": final, **created}
    db.commit()
    return message.outcome
