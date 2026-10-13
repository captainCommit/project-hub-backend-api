"""The read-only tools the assistant can call.

Each tool calls the same service method as the matching API endpoint, as the person asking, and serialises
the result through that endpoint's response model. So a tool can never see more than the person could in the
UI: access checks, module visibility and hidden fields all come from the existing code, not from here.

Results are trimmed before they reach the model: fewer fields, at most MAX_ROWS rows, long text cut short.
That keeps cost down and stops one large project from filling the model's context.
"""

import json
import logging
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams
from app.models.project import Project
from app.models.resource import Resource
from app.models.skill import Skill
from app.models.user import User
from app.schemas.comments import CommentRead
from app.schemas.dashboard import AccountDashboardRead
from app.schemas.hierarchy import AccountSidebarRead
from app.schemas.project_overview import ProjectOverviewRead
from app.schemas.raid import (
    AccountAssumptionRead,
    AccountDecisionRead,
    AccountIssueRead,
    AccountRiskRead,
    AssumptionRead,
    DecisionRead,
    IssueRead,
    RiskRead,
)
from app.schemas.search import SearchEntityType, SearchResponse
from app.schemas.tasks import DueTasksRead, TaskRead
from app.services.access import COMMENTABLE_MODELS
from app.services.account_dashboard import AccountDashboardService
from app.services.ai.providers import ToolSpec
from app.services.comments import CommentService
from app.services.hierarchy import HierarchyService
from app.services.project_overview import ProjectOverviewService
from app.services.raid import RaidService
from app.services.search import SearchService
from app.services.tasks import TaskService

logger = logging.getLogger(__name__)

MAX_ROWS = 50
MAX_TEXT = 400
MAX_RESULT_CHARS = 14_000
RECENT_COMMENTS = 10

# Fields the model never needs; dropping them roughly halves what a list costs.
DROPPED_FIELDS = {
    "account_id",
    "created_by",
    "updated_at",
    "sort_order",
    "color",
    "board_position",
    "priority_id",
    "status_id",
    "task_type_id",
    "parent_task_id",
    "sprint_id",
    "assigned_to",
    "portfolio_id",
    "duration_days",
    "is_deleted",
    "deleted_at",
    "wbs",
    "outline_level",
}

# Lists of these records open on the page that lists them (same as notification emails).
LIST_PAGES = {
    "RISK": "/RiskLog",
    "ISSUE": "/IssueLog",
    "ASSUMPTION": "/AssumptionLog",
    "DECISION": "/DecisionLog",
}

SEARCH_TYPES = list(typing.get_args(SearchEntityType))
SEARCHABLE_MODELS: dict[str, Any] = {**COMMENTABLE_MODELS, "RESOURCE": Resource, "SKILL": Skill}


@dataclass
class ToolContext:
    db: Session
    account_id: UUID
    user: User


class ToolInputError(ValueError):
    """The model passed arguments the tool can't use; the message goes back to it so it can retry."""


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    run: Callable[[ToolContext, dict[str, Any]], Any]
    # Shown in the chat while the tool runs, e.g. "Looking up your tasks…".
    progress: Callable[[dict[str, Any]], str]


# --- Trimming ----------------------------------------------------------------------------------------------


def dump(model: Any, data: Any) -> Any:
    """Serialise a service result exactly as its endpoint would."""
    adapter: TypeAdapter[Any] = TypeAdapter(model)
    return adapter.dump_python(adapter.validate_python(data, from_attributes=True), mode="json")


def slim(value: Any) -> Any:
    """Drop empty and internal fields, shorten text, and turn {label, value, ...} option objects into labels."""
    if isinstance(value, dict):
        if set(value) >= {"label", "value"} and "id" in value and len(value) <= 5:
            return value["label"]
        result = {}
        for key, item in value.items():
            if key in DROPPED_FIELDS or item is None or item == [] or item == {}:
                continue
            result[key] = slim(item)
        return result
    if isinstance(value, list):
        return [slim(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[: MAX_TEXT - 1].rstrip() + "…"
    return value


def capped(rows: list[Any], total: int | None = None) -> dict[str, Any]:
    total = len(rows) if total is None else total
    result: dict[str, Any] = {"items": rows[:MAX_ROWS], "total": total}
    if total > MAX_ROWS:
        result["note"] = f"Showing the first {MAX_ROWS} of {total}. Narrow the question to see others."
    return result


def to_text(result: Any) -> str:
    """JSON for the model, shortened if one result would be too large."""
    text = json.dumps(result, default=str, ensure_ascii=False, separators=(",", ":"))
    while len(text) > MAX_RESULT_CHARS and isinstance(result, dict) and len(result.get("items") or []) > 1:
        result = {**result, "items": result["items"][: len(result["items"]) // 2]}
        result["note"] = f"Showing {len(result['items'])} of {result.get('total')}. Narrow the question to see others."
        text = json.dumps(result, default=str, ensure_ascii=False, separators=(",", ":"))
    return text[:MAX_RESULT_CHARS]


def task_link(project_id: object, task_id: object) -> str:
    return f"/projects/{project_id}/sheet?task={task_id}"


def matches_status(row: dict[str, Any], wanted: str | None) -> bool:
    if not wanted:
        return True
    status = row.get("status")
    label = status if isinstance(status, str) else ""
    return wanted.strip().lower() in label.lower()


def parse_uuid(value: Any, name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ToolInputError(f"{name} must be an id returned by another tool, such as list_projects or search.") from exc


def require_in_account(ctx: ToolContext, project_id: UUID) -> Project:
    project = ctx.db.get(Project, project_id)
    if project is None or project.account_id != ctx.account_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


# --- Tools -------------------------------------------------------------------------------------------------


def my_work(ctx: ToolContext, args: dict[str, Any]) -> Any:
    days = max(0, min(int(args.get("days") or 14), 60))
    data = TaskService(ctx.db).list_due_tasks(
        account_id=ctx.account_id, current_user=ctx.user, mode="BOTH", days=days, assigned_to=ctx.user.id
    )
    rows = []
    for task in dump(DueTasksRead, data)["tasks"]:
        row = slim(task)
        row["project"] = task["project"]["name"]
        row["program"] = task["program"]["name"]
        row["link"] = task_link(task["project_id"], task["id"])
        rows.append(row)
    return {"days_ahead": days, **capped(rows)}


def search(ctx: ToolContext, args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if len(query) < 2:
        raise ToolInputError("query needs at least 2 characters.")
    kinds = [str(kind).upper() for kind in args.get("types") or [] if str(kind).upper() in SEARCH_TYPES]
    data = SearchService(ctx.db).search(
        q=query, current_user=ctx.user, entity_types=",".join(kinds) or None, limit=MAX_ROWS
    )
    rows = []
    # Search covers every account the person belongs to; the assistant only answers about this one.
    for result in dump(SearchResponse, data)["results"]:
        model = SEARCHABLE_MODELS.get(result["entity_type"])
        record = ctx.db.get(model, UUID(result["id"])) if model is not None else None
        if record is None or getattr(record, "account_id", None) != ctx.account_id:
            continue
        row = slim({key: value for key, value in result.items() if key != "score"})
        if result["entity_type"] == "TASK":
            row["link"] = task_link(record.project_id, record.id)
        elif result["entity_type"] == "PROJECT":
            row["link"] = f"/projects/{record.id}/sheet"
        elif result["entity_type"] in LIST_PAGES:
            row["link"] = LIST_PAGES[result["entity_type"]]
        rows.append(row)
    return capped(rows)


def list_projects(ctx: ToolContext, args: dict[str, Any]) -> Any:
    data = dump(AccountSidebarRead, HierarchyService(ctx.db).get_sidebar(account_id=ctx.account_id, current_user=ctx.user))
    portfolios = []
    for portfolio in data["portfolios"]:
        programs = []
        for program in portfolio["programs"]:
            projects = [{"id": p["id"], "name": p["name"], "link": f"/projects/{p['id']}/sheet"} for p in program["projects"]]
            programs.append({"id": program["id"], "name": program["name"], "projects": projects})
        portfolios.append({"id": portfolio["id"], "name": portfolio["name"], "programs": programs})
    return {"portfolios": portfolios}


def project_overview(ctx: ToolContext, args: dict[str, Any]) -> Any:
    project = require_in_account(ctx, parse_uuid(args.get("project_id"), "project_id"))
    data = ProjectOverviewService(ctx.db).get_project_overview(project_id=project.id, current_user=ctx.user)
    return {**slim(dump(ProjectOverviewRead, data)), "link": f"/projects/{project.id}/sheet"}


def account_dashboard(ctx: ToolContext, args: dict[str, Any]) -> Any:
    filters = {name: parse_uuid(args[name], name) for name in ("portfolio_id", "program_id", "project_id") if args.get(name)}
    data = AccountDashboardService(ctx.db).get_account_dashboard(
        account_id=ctx.account_id, current_user=ctx.user, **filters
    )
    return slim(dump(AccountDashboardRead, data))


def list_tasks(ctx: ToolContext, args: dict[str, Any]) -> Any:
    project = require_in_account(ctx, parse_uuid(args.get("project_id"), "project_id"))
    data = TaskService(ctx.db).list_tasks(project_id=project.id, current_user=ctx.user)
    rows = []
    for task in dump(list[TaskRead], data):
        row = slim(task)
        if not matches_status(row, args.get("status")):
            continue
        row["link"] = task_link(project.id, task["id"])
        rows.append(row)
    return {"project": project.name, **capped(rows)}


RAID_KINDS: dict[str, tuple[str, Any, Any]] = {
    # kind: (service method, list response model, entity type)
    "risk": ("list_account_risks", AccountRiskRead, "RISK"),
    "issue": ("list_account_issues", AccountIssueRead, "ISSUE"),
    "assumption": ("list_account_assumptions", AccountAssumptionRead, "ASSUMPTION"),
    "decision": ("list_account_decisions", AccountDecisionRead, "DECISION"),
}


def list_raid_items(ctx: ToolContext, args: dict[str, Any]) -> Any:
    kind = str(args.get("kind") or "").lower()
    if kind not in RAID_KINDS:
        raise ToolInputError(f"kind must be one of: {', '.join(RAID_KINDS)}.")
    method, model, entity_type = RAID_KINDS[kind]
    filters: dict[str, Any] = {}
    if args.get("project_id"):
        filters["project_id"] = require_in_account(ctx, parse_uuid(args["project_id"], "project_id")).id
    if args.get("search"):
        filters["search"] = str(args["search"])
    data = getattr(RaidService(ctx.db), method)(
        account_id=ctx.account_id, current_user=ctx.user, pagination=PaginationParams(paginated=False), **filters
    )
    rows = []
    for item in dump(list[model], data):
        row = slim(item)
        if not matches_status(row, args.get("status")):
            continue
        if isinstance(item.get("project"), dict):
            row["project"] = item["project"].get("name")
        if isinstance(item.get("program"), dict):
            row["program"] = item["program"].get("name")
        row["link"] = LIST_PAGES[entity_type]
        rows.append(row)
    return {"kind": kind, **capped(rows)}


ITEM_KINDS: dict[str, tuple[str, str, Any, str]] = {
    # kind: (service attribute, method, response model, entity type)
    "task": ("task", "get_task", TaskRead, "TASK"),
    "risk": ("raid", "get_risk", RiskRead, "RISK"),
    "issue": ("raid", "get_issue", IssueRead, "ISSUE"),
    "assumption": ("raid", "get_assumption", AssumptionRead, "ASSUMPTION"),
    "decision": ("raid", "get_decision", DecisionRead, "DECISION"),
}


def get_item(ctx: ToolContext, args: dict[str, Any]) -> Any:
    kind = str(args.get("kind") or "").lower()
    if kind not in ITEM_KINDS:
        raise ToolInputError(f"kind must be one of: {', '.join(ITEM_KINDS)}.")
    service_name, method, model, entity_type = ITEM_KINDS[kind]
    item_id = parse_uuid(args.get("id"), "id")
    record = ctx.db.get(COMMENTABLE_MODELS[entity_type], item_id)
    if record is None or record.account_id != ctx.account_id:
        raise HTTPException(status_code=404, detail="Not found.")
    service = TaskService(ctx.db) if service_name == "task" else RaidService(ctx.db)
    id_argument = {"task": "task_id"}.get(kind, f"{kind}_id")
    item = dump(model, getattr(service, method)(**{id_argument: item_id, "current_user": ctx.user}))
    comments = CommentService(ctx.db).list_comments(entity_type=entity_type, entity_id=item_id, current_user=ctx.user)
    recent = [
        {"by": c.get("created_by_name"), "at": c.get("created_at"), "text": c.get("body")}
        for c in dump(list[CommentRead], comments)[-RECENT_COMMENTS:]
    ]
    row = slim(item)
    row["link"] = task_link(item["project_id"], item["id"]) if kind == "task" else LIST_PAGES[entity_type]
    if recent:
        row["recent_comments"] = slim(recent)
    return row


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


ID = {"type": "string", "description": "An id returned by another tool."}

TOOLS: list[Tool] = [
    Tool(
        ToolSpec(
            "my_work",
            "The signed-in person's own tasks that are overdue or due soon (what the My work page shows).",
            _object({"days": {"type": "integer", "description": "How many days ahead to include (default 14, max 60)."}}),
        ),
        my_work,
        lambda args: "Looking up your tasks…",
    ),
    Tool(
        ToolSpec(
            "search",
            "Find portfolios, programs, projects, tasks, risks, issues, assumptions, decisions, sprints, resources "
            "or skills by name or text. Use it to get ids for the other tools.",
            _object(
                {
                    "query": {"type": "string", "description": "Words to look for (at least 2 characters)."},
                    "types": {
                        "type": "array",
                        "items": {"type": "string", "enum": SEARCH_TYPES},
                        "description": "Only these kinds of records (optional).",
                    },
                },
                ["query"],
            ),
        ),
        search,
        lambda args: f"Searching for “{str(args.get('query') or '')[:40]}”…",
    ),
    Tool(
        ToolSpec(
            "list_projects",
            "Every portfolio, program and project the person can see, with their ids.",
            _object({}),
        ),
        list_projects,
        lambda args: "Looking up projects…",
    ),
    Tool(
        ToolSpec(
            "project_overview",
            "One project's health: schedule, task progress, open risks and issues, and key dates.",
            _object({"project_id": ID}, ["project_id"]),
        ),
        project_overview,
        lambda args: "Reading the project overview…",
    ),
    Tool(
        ToolSpec(
            "account_dashboard",
            "Portfolio-level dashboard: project health, overdue work and open RAID items across everything the "
            "person can see, optionally narrowed to one portfolio, program or project.",
            _object({"portfolio_id": ID, "program_id": ID, "project_id": ID}),
        ),
        account_dashboard,
        lambda args: "Reading the dashboard…",
    ),
    Tool(
        ToolSpec(
            "list_tasks",
            "Tasks in one project, optionally only those whose status contains some text (for example \"blocked\").",
            _object({"project_id": ID, "status": {"type": "string"}}, ["project_id"]),
        ),
        list_tasks,
        lambda args: "Looking up tasks…",
    ),
    Tool(
        ToolSpec(
            "list_raid_items",
            "Risks, issues, assumptions or decisions across the account, optionally for one project, matching some "
            "text, or with a status containing some text (for example \"escalated\").",
            _object(
                {
                    "kind": {"type": "string", "enum": list(RAID_KINDS)},
                    "project_id": ID,
                    "status": {"type": "string"},
                    "search": {"type": "string"},
                },
                ["kind"],
            ),
        ),
        list_raid_items,
        lambda args: f"Looking up {str(args.get('kind') or 'RAID item')}s…",
    ),
    Tool(
        ToolSpec(
            "get_item",
            "One task, risk, issue, assumption or decision in full, with its most recent comments.",
            _object({"kind": {"type": "string", "enum": list(ITEM_KINDS)}, "id": ID}, ["kind", "id"]),
        ),
        get_item,
        lambda args: f"Reading the {str(args.get('kind') or 'item')}…",
    ),
]
TOOLS_BY_NAME = {tool.spec.name: tool for tool in TOOLS}
TOOL_SPECS = [tool.spec for tool in TOOLS]


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
    """The tool's result as text for the model. Failures come back as text too, so the model can recover."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return to_text({"error": f"There is no tool called {name}."})
    try:
        return to_text(tool.run(ctx, args))
    except ToolInputError as exc:
        return to_text({"error": str(exc)})
    except HTTPException as exc:
        # The same refusal the person would get in the UI. Don't say which: not found and no access look alike.
        if exc.status_code in (403, 404):
            return to_text({"error": "Not found, or the person doesn't have access to it."})
        return to_text({"error": str(exc.detail)})
    except Exception:
        ctx.db.rollback()
        logger.exception("AI tool failed", extra={"tool": name})
        return to_text({"error": "This lookup failed. Try a different question."})


def progress_label(name: str, args: dict[str, Any]) -> str:
    tool = TOOLS_BY_NAME.get(name)
    return tool.progress(args) if tool else "Working…"
