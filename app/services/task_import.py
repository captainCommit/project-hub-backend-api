"""Creating many tasks from spreadsheet rows, all or nothing."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.access_grant import ProjectModule
from app.models.account_member import AccountMember
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.user import User
from app.schemas.tasks import TaskImportError, TaskImportRead, TaskImportRequest, TaskImportRow
from app.services.activity import ActivityLogService
from app.services.tasks import TaskService


@dataclass
class Options:
    """Active task options of one kind, by lower-cased label and value."""

    ids: dict[str, UUID]
    labels: list[str]


@dataclass
class ParsedRow:
    # Position in the file, so later rows can name this one as their parent.
    index: int
    name: str
    description: str | None
    task_type_id: UUID | None
    status_id: UUID | None
    priority_id: UUID | None
    start_date: date | None
    finish_date: date | None
    duration_days: Decimal | None
    story_points: int | None
    percent_complete: Decimal
    assignee_id: UUID | None
    sprint_id: UUID | None
    # Either an earlier row's index or an existing task's id.
    parent_row: int | None
    parent_task_id: UUID | None


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class TaskImportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tasks = TaskService(db)

    def import_tasks(self, *, project_id: UUID, import_in: TaskImportRequest, current_user: User) -> TaskImportRead:
        project = self.tasks.get_project_or_404(project_id)
        self.tasks.require_project_access(project.id, current_user, module=ProjectModule.TASKS)

        options = {name: self.option_lookup(project.account_id, name) for name in ("TYPE", "STATUS", "PRIORITY")}
        sprints = {
            s.name.strip().lower(): s.id
            for s in self.db.scalars(select(Sprint).where(Sprint.project_id == project.id))
        }
        members = {
            email.lower(): user_id
            for user_id, email in self.db.execute(
                select(User.id, User.email)
                .join(AccountMember, AccountMember.user_id == User.id)
                .where(AccountMember.account_id == project.account_id)
            )
        }
        existing: dict[str, UUID] = {}
        for task in self.tasks.tasks.list_tasks_for_project(project.id):
            existing.setdefault(task.name.strip().lower(), task.id)

        parsed: list[ParsedRow] = []
        errors: list[TaskImportError] = []
        earlier_rows: dict[str, int] = {}
        for index, row in enumerate(import_in.rows):
            problems: list[str] = []
            result = self.parse_row(index, row, options, sprints, members, existing, earlier_rows, problems)
            if problems:
                errors.append(TaskImportError(row=index + 1, message="; ".join(problems)))
            elif result is not None:
                parsed.append(result)
                earlier_rows[result.name.lower()] = index

        if errors or import_in.dry_run:
            return TaskImportRead(created=0, dry_run=import_in.dry_run, errors=errors)

        self.create_all(project, parsed, current_user)
        return TaskImportRead(created=len(parsed), dry_run=False, errors=[])

    def parse_row(
        self,
        index: int,
        row: TaskImportRow,
        options: dict[str, Options],
        sprints: dict[str, UUID],
        members: dict[str, UUID],
        existing: dict[str, UUID],
        earlier_rows: dict[str, int],
        problems: list[str],
    ) -> ParsedRow | None:
        name = _text(row.name)
        if name is None:
            problems.append("Name is required")
        elif len(name) > 255:
            problems.append("Name is longer than 255 characters")

        def option(field: str, option_name: str, label: str) -> UUID | None:
            value = _text(getattr(row, field))
            if value is None:
                return None
            option_id = options[option_name].ids.get(value.lower())
            if option_id is None:
                choices = ", ".join(options[option_name].labels)
                problems.append(f'Unknown {label} "{value}"' + (f" (use one of: {choices})" if choices else ""))
            return option_id

        def day(field: str, label: str) -> date | None:
            value = _text(getattr(row, field))
            if value is None:
                return None
            try:
                return date.fromisoformat(value.replace("/", "-"))
            except ValueError:
                problems.append(f'{label} "{value}" is not a date (use YYYY-MM-DD)')
                return None

        def number(field: str, label: str) -> Decimal | None:
            value = _text(getattr(row, field))
            if value is None:
                return None
            try:
                return Decimal(value.rstrip("%").strip())
            except InvalidOperation:
                problems.append(f'{label} "{value}" is not a number')
                return None

        task_type_id = option("type", "TYPE", "type")
        status_id = option("status", "STATUS", "status")
        priority_id = option("priority", "PRIORITY", "priority")
        start_date = day("start_date", "Start date")
        finish_date = day("finish_date", "Finish date")
        if start_date and finish_date and finish_date < start_date:
            problems.append("Finish date is before the start date")

        duration_days = number("duration_days", "Duration")
        if duration_days is not None and duration_days < 0:
            problems.append("Duration can't be negative")
        story_points_value = number("story_points", "Story points")
        story_points = None
        if story_points_value is not None:
            if story_points_value != story_points_value.to_integral_value() or story_points_value <= 0:
                problems.append("Story points must be a whole number above 0")
            else:
                story_points = int(story_points_value)
        percent_complete = number("percent_complete", "% complete")
        if percent_complete is not None and not Decimal(0) <= percent_complete <= Decimal(100):
            problems.append("% complete must be between 0 and 100")

        assignee_id = None
        assignee = _text(row.assignee)
        if assignee is not None:
            assignee_id = members.get(assignee.lower())
            if assignee_id is None:
                problems.append(f'No account member with the email "{assignee}"')

        sprint_id = None
        sprint = _text(row.sprint)
        if sprint is not None:
            sprint_id = sprints.get(sprint.lower())
            if sprint_id is None:
                problems.append(f'No sprint named "{sprint}" in this project')

        parent_row = parent_task_id = None
        parent = _text(row.parent)
        if parent is not None:
            parent_row = earlier_rows.get(parent.lower())
            if parent_row is None:
                parent_task_id = existing.get(parent.lower())
                if parent_task_id is None:
                    problems.append(f'Parent "{parent}" is not an earlier row or an existing task')

        if problems or name is None:
            return None
        return ParsedRow(
            index=index,
            name=name,
            description=_text(row.description),
            task_type_id=task_type_id,
            status_id=status_id,
            priority_id=priority_id,
            start_date=start_date,
            finish_date=finish_date,
            duration_days=duration_days,
            story_points=story_points,
            percent_complete=percent_complete if percent_complete is not None else Decimal(0),
            assignee_id=assignee_id,
            sprint_id=sprint_id,
            parent_row=parent_row,
            parent_task_id=parent_task_id,
        )

    def create_all(self, project: Project, rows: list[ParsedRow], current_user: User) -> None:
        repository = self.tasks.tasks
        default_type = repository.get_default_task_option_id(account_id=project.account_id, option_name="TYPE")
        default_status = repository.get_default_task_option_id(account_id=project.account_id, option_name="STATUS")
        next_sort: dict[UUID | None, Decimal] = {}
        created: dict[int, Task] = {}
        activity = ActivityLogService(self.db)
        try:
            for row in rows:
                parent_id = row.parent_task_id
                if row.parent_row is not None:
                    parent_id = created[row.parent_row].id
                if parent_id not in next_sort:
                    next_sort[parent_id] = self.tasks.next_child_sort_order(
                        project_id=project.id, parent_task_id=parent_id  # type: ignore[arg-type]
                    )
                sort_order = next_sort[parent_id]
                next_sort[parent_id] = sort_order + 1
                task = repository.create_task(
                    account_id=project.account_id,
                    project_id=project.id,
                    sprint_id=row.sprint_id,
                    parent_task_id=parent_id,
                    task_type_id=row.task_type_id or default_type,
                    status_id=row.status_id or default_status,
                    priority_id=row.priority_id,
                    name=row.name,
                    description=row.description,
                    start_date=row.start_date,
                    finish_date=row.finish_date,
                    duration_days=row.duration_days,
                    story_points=row.story_points,
                    percent_complete=row.percent_complete,
                    sort_order=sort_order,
                    created_by=current_user.id,
                )
                if row.assignee_id is not None:
                    repository.create_assignment(
                        account_id=project.account_id, task_id=task.id, user_id=row.assignee_id, resource_name=None
                    )
                activity.record(
                    account_id=task.account_id,
                    entity_type="TASK",
                    entity_id=task.id,
                    action="CREATED",
                    new_values={"name": task.name, "project_id": task.project_id, "status_id": task.status_id, "imported": True},
                    created_by=current_user.id,
                )
                created[row.index] = task
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def option_lookup(self, account_id: UUID, option_name: str) -> Options:
        values = self.db.scalars(
            select(OptionValue)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionSet.account_id == account_id,
                OptionSet.entity_type == "TASK",
                OptionSet.name == option_name,
                OptionValue.is_active.is_(True),
            )
            .order_by(OptionValue.sort_order, func.lower(OptionValue.label))
        ).all()
        ids: dict[str, UUID] = {}
        for value in values:
            ids.setdefault(value.value.strip().lower(), value.id)
            ids.setdefault(value.label.strip().lower(), value.id)
        return Options(ids=ids, labels=[value.label for value in values])
