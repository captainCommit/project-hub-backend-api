"""Commands people type in Slack (/projecthub ...), run as their linked Project Hub user.

Every command calls the same services as the app (the assistant's read tools for lookups, TaskService for
creating), so permissions apply exactly as they do in the UI.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.hierarchy import AccountSidebarRead
from app.schemas.tasks import TaskCreate
from app.services.ai.tools import ToolContext, dump, my_work, search, task_link
from app.services.hierarchy import HierarchyService
from app.services.integrations.messages import ChatMessage
from app.services.tasks import TaskService

LIST_LIMIT = 10

HELP_LINES = [
    "*mine*: your overdue and upcoming tasks",
    "*search* _words_: find projects, tasks, risks and more",
    "*new* _task name_ *in* _project name_: add a task",
    "*link*: connect your Project Hub account",
]


@dataclass
class CommandContext:
    db: Session
    account_id: Any
    user: User


def help_message(prefix: str) -> ChatMessage:
    return ChatMessage(title="What I can do", lines=[f"{prefix} {line}" for line in HELP_LINES])


def run_command(ctx: CommandContext, text: str, *, prefix: str) -> ChatMessage:
    words = text.strip().split(maxsplit=1)
    verb = words[0].lower() if words else "help"
    rest = words[1].strip() if len(words) > 1 else ""
    try:
        if verb in ("mine", "my", "tasks", "me"):
            return mine(ctx)
        if verb in ("search", "find"):
            return find(ctx, rest)
        if verb in ("new", "add", "create", "task"):
            return new_task(ctx, rest, prefix=prefix)
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            return ChatMessage(title="Not found, or you don't have access to it.")
        return ChatMessage(title=str(exc.detail))
    return help_message(prefix)


def mine(ctx: CommandContext) -> ChatMessage:
    result = my_work(ToolContext(ctx.db, ctx.account_id, ctx.user), {"days": 14})
    tasks = result["items"]
    if not tasks:
        return ChatMessage(title="Nothing overdue or due in the next two weeks. 🎉", link="/", link_label="Open Home")
    items = []
    for task in tasks[:LIST_LIMIT]:
        due = task.get("finish_date")
        late = " (overdue)" if task.get("due_status") == "OVERDUE" else ""
        items.append((f"{task['name']}: {task.get('project', '')}" + (f", due {due}{late}" if due else ""), task["link"]))
    title = f"You have {result['total']} task{'s' if result['total'] != 1 else ''} due soon or overdue"
    return ChatMessage(title=title, items=items, link="/", link_label="Open Home")


def find(ctx: CommandContext, query: str) -> ChatMessage:
    if len(query) < 2:
        return ChatMessage(title="Type at least two characters to search for.")
    result = search(ToolContext(ctx.db, ctx.account_id, ctx.user), {"query": query})
    rows = result["items"]
    if not rows:
        return ChatMessage(title=f"Nothing found for “{query}”.")
    items = []
    for row in rows[:LIST_LIMIT]:
        name = row.get("title") or row.get("name") or "Untitled"
        kind = str(row.get("entity_type", "")).replace("_", " ").lower()
        items.append((f"{name} ({kind})", row.get("link")))
    return ChatMessage(title=f"Results for “{query}”", items=items)


def new_task(ctx: CommandContext, text: str, *, prefix: str) -> ChatMessage:
    name, separator, project_name = text.rpartition(" in ")
    if not separator or not name.strip() or not project_name.strip():
        return ChatMessage(title=f"Try: {prefix} new Draft the launch plan in Apollo")
    project = find_project(ctx, project_name.strip())
    if isinstance(project, ChatMessage):
        return project
    try:
        task = TaskService(ctx.db).create_task(project_id=UUID(str(project["id"])), task_in=TaskCreate(name=name.strip()[:255]), current_user=ctx.user)
    except ValidationError:
        return ChatMessage(title="That task name isn't valid.")
    ctx.db.commit()
    return ChatMessage(title=f"Added “{task['name']}” to {project['name']}", link=task_link(project["id"], task["id"]), link_label="Open task")


def find_project(ctx: CommandContext, wanted: str) -> dict[str, Any] | ChatMessage:
    """The one visible project whose name matches: exactly (ignoring case) if possible, else by containing it."""
    sidebar = dump(AccountSidebarRead, HierarchyService(ctx.db).get_sidebar(account_id=ctx.account_id, current_user=ctx.user))
    projects = [p for portfolio in sidebar["portfolios"] for program in portfolio["programs"] for p in program["projects"]]
    exact = [p for p in projects if p["name"].lower() == wanted.lower()]
    matches = exact or [p for p in projects if wanted.lower() in p["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return ChatMessage(title=f"No project called “{wanted}” that you can see.")
    names = ", ".join(sorted(p["name"] for p in matches[:8]))
    return ChatMessage(title=f"“{wanted}” matches several projects: {names}. Use the full name.")
