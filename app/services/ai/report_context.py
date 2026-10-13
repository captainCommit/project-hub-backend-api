"""What a written report is written from: a project's, program's or portfolio's data as the person who asked sees it.

Everything is read through the same service methods as the API endpoints, as that person, and serialised
through the endpoints' response models (as the assistant's tools are, see app.services.ai.tools). A part the
person can't see, such as a module hidden from them, is left out with a note, never worked around.

Reports need more than a chat answer, but not everything: tasks are cut down to the ones a report talks about
(late, due soon, in progress, blocked, recently finished), registers to open items plus those closed this
period, and activity to this period. If the data is still too large it is built again with smaller lists.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.core.pagination import PaginationParams
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.user import User
from app.schemas.activity import ActivityLogRead
from app.schemas.hierarchy import AccountSidebarRead
from app.schemas.project_logs import ChangeRequestRead
from app.schemas.project_overview import ProjectOverviewRead
from app.schemas.raid import AssumptionRead, DecisionRead, IssueRead, RiskRead
from app.schemas.sprints import SprintRead
from app.schemas.tasks import TaskRead
from app.services.activity import ActivityLogService
from app.services.ai.tools import LIST_PAGES, dump, slim, task_link
from app.services.hierarchy import HierarchyService
from app.services.project_overview import OPEN_EXCLUDED_KEYS, TASK_COMPLETED_KEYS, TASK_IN_PROGRESS_KEYS, ProjectOverviewService
from app.services.raid import RaidService, normalize_option_key
from app.services.sprints import SprintService
from app.services.tasks import TaskService

# About 15,000 tokens of data at most; the instructions and the report itself come on top.
MAX_CONTEXT_CHARS = 60_000
# Rows per list, tried in turn until the data fits.
LIST_LIMITS = (25, 12, 6)
MAX_PROJECTS = 30
ACTIVITY_PAGE = 100
MAX_ACTIVITY_PAGES = 5
DUE_SOON_DAYS = 14
NOT_SHARED = "Not shared with this person."
# Already known from the report's project, or noise in a register.
REGISTER_DROPPED = {"id", "project_id", "program_id", "project", "program", "comment_count", "created_by_name", "created_at"}

REGISTERS: dict[str, tuple[Any, str]] = {
    # kind: (response model, entity type for the page link)
    "risk": (RiskRead, "RISK"),
    "issue": (IssueRead, "ISSUE"),
    "assumption": (AssumptionRead, "ASSUMPTION"),
    "decision": (DecisionRead, "DECISION"),
    "change_request": (ChangeRequestRead, ""),
}


@dataclass(frozen=True)
class ReportScope:
    kind: str  # PROJECT, PROGRAM or PORTFOLIO
    record: Project | Program | Portfolio


def option_keys(value: Any) -> set[str]:
    """The normalised value and label of an option object as the API returns it ({id, label, value, color})."""
    if not isinstance(value, dict):
        return set()
    return {normalize_option_key(str(value.get("value") or "")), normalize_option_key(str(value.get("label") or ""))}


def as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def since_start(since: date) -> datetime:
    return datetime.combine(since, time.min, tzinfo=UTC)


def visible(read: Callable[[], Any]) -> tuple[bool, Any]:
    """Run one lookup; a refusal (hidden module, no access) becomes (False, None) instead of an error."""
    try:
        return True, read()
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            return False, None
        raise


class ReportContextBuilder:
    def __init__(self, db: Any, *, account_id: UUID, user: User, since: date, today: date | None = None) -> None:
        self.db = db
        self.account_id = account_id
        self.user = user
        self.since = since
        self.today = today or date.today()

    def build(self, scope: ReportScope, *, with_activity: bool, raid_focus: bool = False) -> str:
        """The data as compact JSON, at most MAX_CONTEXT_CHARS long."""
        text = ""
        for limit in LIST_LIMITS:
            if scope.kind == "PROJECT":
                data = self.project(scope.record, limit=limit, with_activity=with_activity, raid_focus=raid_focus)  # type: ignore[arg-type]
            else:
                data = self.group(scope, limit=limit, with_activity=with_activity)
            text = json.dumps(data, default=str, ensure_ascii=False, separators=(",", ":"))
            if len(text) <= MAX_CONTEXT_CHARS:
                return text
        return text[:MAX_CONTEXT_CHARS]

    # One project ----------------------------------------------------------------------------------------

    def project(self, project: Project, *, limit: int, with_activity: bool, raid_focus: bool = False) -> dict[str, Any]:
        overview = dump(ProjectOverviewRead, ProjectOverviewService(self.db).get_project_overview(project_id=project.id, current_user=self.user))
        data: dict[str, Any] = {
            "project": {**slim(overview["project"]), "link": f"/projects/{project.id}/sheet"},
            "health": overview["health"],
            "stats": overview["stats"],
            "upcoming_milestones": slim(overview["upcoming_milestones"][:limit]),
        }
        if not raid_focus:
            data["tasks"] = self.tasks(project, limit=limit)
            data["sprints"] = self.sprints(project, limit=limit)
        data["registers"] = {kind: self.register(project, kind, limit=limit if not raid_focus else limit * 2) for kind in REGISTERS}
        if with_activity:
            data["changes_this_period"] = self.activity(project, limit=limit * 3)
        return data

    def tasks(self, project: Project, *, limit: int) -> Any:
        ok, rows = visible(lambda: TaskService(self.db).list_tasks(project_id=project.id, current_user=self.user))
        if not ok:
            return NOT_SHARED
        groups: dict[str, list[dict[str, Any]]] = {
            "overdue": [],
            "blocked": [],
            "in_progress": [],
            "due_soon": [],
            "finished_this_period": [],
        }
        start = since_start(self.since)
        for task in dump(list[TaskRead], rows):
            keys = option_keys(task.get("status"))
            done = bool(keys & TASK_COMPLETED_KEYS)
            finish = as_date(task.get("finish_date"))
            updated = datetime.fromisoformat(str(task["updated_at"]))
            row = {
                "name": task["name"],
                "status": (task.get("status") or {}).get("label"),
                "priority": (task.get("priority") or {}).get("label"),
                "due": task.get("finish_date"),
                "percent_complete": task.get("percent_complete"),
                "assigned": [a["resource_name"] for a in task.get("assignments") or [] if a.get("resource_name")],
                "sprint": (task.get("sprint") or {}).get("name"),
                "link": task_link(project.id, task["id"]),
            }
            row = slim(row)
            if done:
                if (updated if updated.tzinfo else updated.replace(tzinfo=UTC)) >= start:
                    groups["finished_this_period"].append(row)
            elif any("block" in key for key in keys):
                groups["blocked"].append(row)
            elif finish and finish < self.today:
                groups["overdue"].append(row)
            elif keys & TASK_IN_PROGRESS_KEYS:
                groups["in_progress"].append(row)
            elif finish and finish <= self.today + timedelta(days=DUE_SOON_DAYS):
                groups["due_soon"].append(row)
        return {name: capped(rows, limit) for name, rows in groups.items()}

    def sprints(self, project: Project, *, limit: int) -> Any:
        ok, rows = visible(lambda: SprintService(self.db).list_sprints(project_id=project.id, current_user=self.user))
        if not ok:
            return NOT_SHARED
        current = []
        for sprint in dump(list[SprintRead], rows):
            end = as_date(sprint.get("end_date"))
            closed = bool(option_keys(sprint.get("status")) & OPEN_EXCLUDED_KEYS)
            if (not closed) or (end and end >= self.since):
                current.append(slim({key: sprint.get(key) for key in ("name", "goal", "status", "start_date", "end_date")}))
        return capped(current, min(limit, 5))

    def register(self, project: Project, kind: str, *, limit: int) -> Any:
        model, entity_type = REGISTERS[kind]
        ok, rows = visible(
            lambda: RaidService(self.db).list_items(
                kind=kind, project_id=project.id, current_user=self.user, pagination=PaginationParams(paginated=False)
            )
        )
        if not ok:
            return NOT_SHARED
        start = since_start(self.since)
        kept = []
        for item in dump(list[model], rows):
            is_open = not (option_keys(item.get("status")) & OPEN_EXCLUDED_KEYS)
            updated = datetime.fromisoformat(str(item["updated_at"]))
            changed = (updated if updated.tzinfo else updated.replace(tzinfo=UTC)) >= start
            if not is_open and not changed:
                continue
            row = slim({key: value for key, value in item.items() if key not in REGISTER_DROPPED})
            row["changed_this_period"] = changed
            if entity_type:
                row["link"] = LIST_PAGES[entity_type]
            kept.append(row)
        return capped(kept, limit)

    def activity(self, project: Project, *, limit: int) -> Any:
        """What changed in the project this period, newest first, one line each."""
        service = ActivityLogService(self.db)
        start = since_start(self.since)
        lines: list[str] = []
        total = 0
        for page in range(1, MAX_ACTIVITY_PAGES + 1):
            ok, result = visible(
                lambda: service.list_project_activity(
                    project_id=project.id,
                    current_user=self.user,
                    pagination=PaginationParams(page=page, page_size=ACTIVITY_PAGE, paginated=True),
                )
            )
            if not ok:
                return NOT_SHARED
            entries = dump(list[ActivityLogRead], result["items"])  # type: ignore[index]
            reached_start = False
            for entry in entries:
                at = datetime.fromisoformat(str(entry["created_at"]))
                if (at if at.tzinfo else at.replace(tzinfo=UTC)) < start:
                    reached_start = True
                    break
                total += 1
                if len(lines) < limit:
                    lines.append(activity_line(entry))
            if reached_start or len(entries) < ACTIVITY_PAGE:
                break
        result_rows: dict[str, Any] = {"entries": lines, "total": total}
        if total > len(lines):
            result_rows["note"] = f"Showing the latest {len(lines)} of {total} changes."
        return result_rows

    # A program or portfolio ---------------------------------------------------------------------------

    def group(self, scope: ReportScope, *, limit: int, with_activity: bool) -> dict[str, Any]:
        sidebar = dump(AccountSidebarRead, HierarchyService(self.db).get_sidebar(account_id=self.account_id, current_user=self.user))
        programs = []
        for portfolio in sidebar["portfolios"]:
            for program in portfolio["programs"]:
                if (scope.kind == "PROGRAM" and program["id"] == str(scope.record.id)) or (
                    scope.kind == "PORTFOLIO" and portfolio["id"] == str(scope.record.id)
                ):
                    programs.append(program)
        project_count = sum(len(program["projects"]) for program in programs)
        per_project = max(3, limit // 4)
        shown = 0
        result_programs = []
        for program in programs:
            projects = []
            for item in program["projects"]:
                if shown >= MAX_PROJECTS:
                    break
                project = self.db.get(Project, UUID(item["id"]))
                if project is None:
                    continue
                shown += 1
                projects.append(self.project_summary(project, limit=per_project, with_activity=with_activity))
            result_programs.append({"program": program["name"], "projects": projects})
        data: dict[str, Any] = {
            scope.kind.lower(): {"name": scope.record.name, "description": getattr(scope.record, "description", None)},
            "programs": result_programs,
            "project_count": project_count,
        }
        if project_count > shown:
            data["note"] = f"Showing {shown} of {project_count} projects."
        return slim(data)

    def project_summary(self, project: Project, *, limit: int, with_activity: bool) -> dict[str, Any]:
        ok, overview = visible(
            lambda: dump(ProjectOverviewRead, ProjectOverviewService(self.db).get_project_overview(project_id=project.id, current_user=self.user))
        )
        if not ok:
            return {"name": project.name, "note": NOT_SHARED}
        summary: dict[str, Any] = {
            "name": project.name,
            "link": f"/projects/{project.id}/sheet",
            "status": (overview["project"].get("status") or {}).get("label"),
            "start_date": overview["project"].get("start_date"),
            "target_end_date": overview["project"].get("target_end_date"),
            "health": overview["health"],
            "stats": overview["stats"],
            "upcoming_milestones": slim(overview["upcoming_milestones"][:limit]),
            "top_risks": slim([strip_ids(r) for r in overview["top_risks"][:limit]]),
            "top_issues": slim([strip_ids(i) for i in overview["top_issues"][:limit]]),
            "pending_decisions": slim([strip_ids(d) for d in overview["pending_decisions"][:limit]]),
        }
        if with_activity:
            summary["changes_this_period"] = self.activity(project, limit=limit * 3)
        return summary


def strip_ids(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in ("id", "created_at")}


def capped(rows: list[Any], limit: int) -> Any:
    if len(rows) <= limit:
        return rows
    return {"items": rows[:limit], "total": len(rows), "note": f"Showing {limit} of {len(rows)}."}


def activity_line(entry: dict[str, Any]) -> str:
    """'2026-10-12 Ana Diaz updated task “Late report”: Status Not started → Done'."""
    when = str(entry["created_at"])[:10]
    who = entry.get("actor_name") or "Someone"
    action = str(entry["action"]).lower().replace("_", " ")
    what = str(entry["entity_type"]).lower().replace("_", " ")
    name = entry.get("entity_name")
    line = f"{when} {who} {action} {what}" + (f" “{name}”" if name else "")
    changes = [
        f"{change['label']} {change.get('old') or '—'} → {change.get('new') or '—'}"
        for change in entry.get("changes") or []
    ]
    if changes:
        line += ": " + "; ".join(changes)
    return line[:300]
