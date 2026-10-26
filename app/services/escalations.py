"""Escalating a RAID item to a person or a team (RAID > Risks, Assumptions, Issues, Dependencies).

The people chosen get an ESCALATION notification, which is always emailed straight away (see
NotificationPreferenceService.frequency_for). A team is everyone whose resource record has that department.
Only resources linked to a Project Hub login can be reached; the response says who was skipped and why.
"""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.assumption import Assumption
from app.models.dependency import Dependency
from app.models.issue import Issue
from app.models.notification import EmailStatus, NotificationType
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.resource import Resource
from app.models.risk import Risk
from app.models.user import User
from app.schemas.escalations import EscalationCreate
from app.services.access import access_for, can_see_entity
from app.services.activity import ActivityLogService
from app.services.notifications import NotificationService

ESCALATABLE_MODELS: dict[str, type[Any]] = {
    "RISK": Risk,
    "ISSUE": Issue,
    "ASSUMPTION": Assumption,
    "DEPENDENCY": Dependency,
}
KIND_WORDS = {"RISK": "risk", "ISSUE": "issue", "ASSUMPTION": "assumption", "DEPENDENCY": "dependency"}


def person_name(user: User) -> str:
    return (
        " ".join(part for part in (user.first_name, user.last_name) if part).strip()
        or (user.full_name or "").strip()
        or user.email
    )


class EscalationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.notifications = NotificationService(db)

    def escalate(self, *, entity_type: str, entity_id: UUID, data: EscalationCreate, current_user: User) -> dict:
        kind = entity_type.strip().upper()
        model = ESCALATABLE_MODELS.get(kind)
        if model is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only RAID items can be escalated.")
        record = self.db.get(model, entity_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found.")
        scope = access_for(self.db, account_id=record.account_id, user=current_user)
        scope.require_writable()
        # Escalating is a change to the item, so it needs the same rights as editing it.
        scope.require_entity(kind, record, contribute=True)

        targets: list[str] = []
        candidates: list[Resource] = []
        if data.resource_id is not None:
            resource = self.db.get(Resource, data.resource_id)
            if resource is None or resource.account_id != record.account_id or not resource.is_active:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That person isn't in this account.")
            targets.append(resource.name)
            candidates.append(resource)
        team = (data.team or "").strip()
        if team:
            members = list(
                self.db.scalars(
                    select(Resource)
                    .where(
                        Resource.account_id == record.account_id,
                        Resource.is_active.is_(True),
                        func.lower(func.trim(Resource.department)) == team.lower(),
                    )
                    .order_by(Resource.name)
                ).all()
            )
            if not members:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Nobody is in the {team} team.")
            targets.append(f"{team} team")
            candidates.extend(members)
        escalated_to = " and ".join(targets)

        actor = person_name(current_user)
        kind_word = KIND_WORDS[kind]
        details = {
            "reference": getattr(record, f"{kind.lower()}_number", None),
            "title": getattr(record, "title", None) or getattr(record, "description", None) or "",
            "project": self.project_name(record.project_id),
            "priority": self.option_label(getattr(record, "priority_id", None)),
            "escalated_to": escalated_to,
            "actor": actor,
            "kind": kind_word,
        }
        title = f"{actor} escalated a {kind_word} to {escalated_to}"
        note = (data.note or "").strip() or None

        notified: list[dict] = []
        skipped: list[dict] = []
        seen: set[UUID] = set()
        for resource in candidates:
            if resource.user_id is None:
                skipped.append({"name": resource.name, "reason": "No Project Hub login, so there's no email address."})
                continue
            if resource.user_id in seen:
                continue
            seen.add(resource.user_id)
            if resource.user_id == current_user.id:
                if data.resource_id == resource.id:
                    skipped.append({"name": resource.name, "reason": "That's you."})
                continue
            user = self.db.get(User, resource.user_id)
            if user is None or not user.is_active:
                skipped.append({"name": resource.name, "reason": "Their login is turned off."})
                continue
            if not can_see_entity(self.db, account_id=record.account_id, user=user, entity_type=kind, entity_id=record.id):
                skipped.append({"name": resource.name, "reason": f"They can't open this {kind_word}."})
                continue
            notification = self.notifications.create_notification(
                account_id=record.account_id,
                user_id=user.id,
                entity_type=kind,
                entity_id=record.id,
                notification_type=NotificationType.ESCALATION,
                title=title,
                message=note,
                actor_user_id=current_user.id,
                details=details,
            )
            if notification is None:
                skipped.append({"name": resource.name, "reason": "They aren't a member of this account."})
                continue
            notified.append({"name": resource.name, "emailed": notification.email_status == EmailStatus.PENDING.value})

        if not notified:
            self.db.rollback()
            reasons = "; ".join(f"{s['name']}: {s['reason']}" for s in skipped) or "Nobody to notify."
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Nobody could be notified. {reasons}")

        ActivityLogService(self.db).record(
            account_id=record.account_id,
            entity_type=kind,
            entity_id=record.id,
            action="ESCALATED",
            new_values={"escalated_to": escalated_to, "note": note},
            created_by=current_user.id,
        )
        self.db.commit()
        return {"escalated_to": escalated_to, "notified": notified, "skipped": skipped}

    def project_name(self, project_id: UUID | None) -> str | None:
        project = self.db.get(Project, project_id) if project_id else None
        return project.name if project else None

    def option_label(self, option_id: UUID | None) -> str | None:
        option = self.db.get(OptionValue, option_id) if option_id else None
        return option.label if option else None
