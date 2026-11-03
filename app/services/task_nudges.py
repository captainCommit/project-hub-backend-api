"""Nudging the people on a task about its due date (project schedule > Audit > Nudge).

Everyone assigned to the task gets a DUE_SOON notification, so it is emailed as their due-soon reminders are.
Assignees reached through a resource need that resource linked to a Project Hub login; the response says who
was skipped and why.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.notification import NotificationType
from app.models.project import Project
from app.models.resource import Resource
from app.models.task import Task
from app.models.task_assignment import TaskAssignment
from app.models.user import User
from app.services.access import access_for, can_see_entity
from app.services.activity import ActivityLogService
from app.services.escalations import person_name
from app.services.notifications import NotificationService


class TaskNudgeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.notifications = NotificationService(db)

    def nudge(self, *, task_id: UUID, note: str | None, current_user: User) -> dict:
        task = self.db.get(Task, task_id)
        if task is None or task.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        scope = access_for(self.db, account_id=task.account_id, user=current_user)
        scope.require_writable()
        scope.require_entity("TASK", task)

        assignments = list(self.db.scalars(select(TaskAssignment).where(TaskAssignment.task_id == task.id)).all())
        if not assignments:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nobody is assigned to this task yet.")

        # (display name, user id or None) for each assignee.
        people: list[tuple[str, UUID | None]] = []
        for assignment in assignments:
            if assignment.user_id is not None:
                user = self.db.get(User, assignment.user_id)
                people.append((person_name(user) if user else (assignment.resource_name or "Unknown"), assignment.user_id))
                continue
            name = (assignment.resource_name or "").strip()
            resource = self.db.scalars(
                select(Resource).where(
                    Resource.account_id == task.account_id,
                    func.lower(func.trim(Resource.name)) == name.lower(),
                )
            ).first()
            people.append((name or "Unknown", resource.user_id if resource else None))

        actor = person_name(current_user)
        project = self.db.get(Project, task.project_id)
        due = f"{task.finish_date:%b} {task.finish_date.day}, {task.finish_date.year}" if task.finish_date else None
        title = f"{actor} nudged you about \"{task.name}\"" + (f" (due {due})" if due else "")
        details = {
            "task": task.name,
            "project": project.name if project else None,
            "due_date": task.finish_date.isoformat() if task.finish_date else None,
            "actor": actor,
        }
        note = (note or "").strip() or None

        notified: list[str] = []
        skipped: list[dict] = []
        seen: set[UUID] = set()
        for name, user_id in people:
            if user_id is None:
                skipped.append({"name": name, "reason": "No Project Hub login, so they can't be notified."})
                continue
            if user_id in seen:
                continue
            seen.add(user_id)
            if user_id == current_user.id:
                skipped.append({"name": name, "reason": "That's you."})
                continue
            user = self.db.get(User, user_id)
            if user is None or not user.is_active:
                skipped.append({"name": name, "reason": "Their login is turned off."})
                continue
            if not can_see_entity(self.db, account_id=task.account_id, user=user, entity_type="TASK", entity_id=task.id):
                skipped.append({"name": name, "reason": "They can't open this task."})
                continue
            notification = self.notifications.create_notification(
                account_id=task.account_id,
                user_id=user_id,
                entity_type="TASK",
                entity_id=task.id,
                notification_type=NotificationType.DUE_SOON,
                title=title,
                message=note,
                actor_user_id=current_user.id,
                details=details,
            )
            if notification is None:
                skipped.append({"name": name, "reason": "They aren't a member of this account."})
                continue
            notified.append(name)

        if not notified:
            self.db.rollback()
            reasons = "; ".join(f"{s['name']}: {s['reason']}" for s in skipped) or "Nobody to notify."
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Nobody could be nudged. {reasons}")

        ActivityLogService(self.db).record(
            account_id=task.account_id,
            entity_type="TASK",
            entity_id=task.id,
            action="NUDGED",
            new_values={"notified": notified, "note": note},
            created_by=current_user.id,
        )
        self.db.commit()
        return {"notified": notified, "skipped": skipped}
