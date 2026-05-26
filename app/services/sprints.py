from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.sprint import Sprint
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.repositories.sprints import SprintRepository
from app.schemas.sprints import SprintCreate, SprintUpdate
from app.services.activity import ActivityLogService


SPRINT_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}


class SprintService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.hierarchy = HierarchyRepository(db)
        self.sprints = SprintRepository(db)

    def list_sprints(self, *, project_id: UUID, current_user: User) -> list[dict[str, object]]:
        project = self.get_project_or_404(project_id)
        self.require_account_member(account_id=project.account_id, user_id=current_user.id)
        return self.enrich_sprints(self.sprints.list_for_project(project.id))

    def create_sprint(self, *, project_id: UUID, sprint_in: SprintCreate, current_user: User) -> dict[str, object]:
        project = self.get_project_or_404(project_id)
        self.require_account_role(
            account_id=project.account_id,
            user_id=current_user.id,
            allowed_roles=SPRINT_WRITE_ROLES,
        )
        status_id = self.resolve_status_id(account_id=project.account_id, status_id=sprint_in.status_id)
        sprint = self.sprints.create(
            account_id=project.account_id,
            project_id=project.id,
            name=sprint_in.name,
            goal=sprint_in.goal,
            status_id=status_id,
            start_date=sprint_in.start_date,
            end_date=sprint_in.end_date,
            created_by=current_user.id,
        )
        ActivityLogService(self.db).record(
            account_id=sprint.account_id,
            entity_type="SPRINT",
            entity_id=sprint.id,
            action="SPRINT_CREATED",
            new_values={"name": sprint.name, "project_id": sprint.project_id, "status_id": sprint.status_id},
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(sprint)
        return self.enrich_sprint(sprint)

    def get_sprint(self, *, sprint_id: UUID, current_user: User) -> dict[str, object]:
        sprint = self.get_sprint_or_404(sprint_id)
        self.require_account_member(account_id=sprint.account_id, user_id=current_user.id)
        return self.enrich_sprint(sprint)

    def update_sprint(self, *, sprint_id: UUID, sprint_in: SprintUpdate, current_user: User) -> dict[str, object]:
        sprint = self.get_sprint_or_404(sprint_id)
        self.require_account_role(
            account_id=sprint.account_id,
            user_id=current_user.id,
            allowed_roles=SPRINT_WRITE_ROLES,
        )
        changes = sprint_in.model_dump(exclude_unset=True)
        if "status_id" in changes and changes["status_id"] is not None:
            changes["status_id"] = self.validate_status_id(account_id=sprint.account_id, status_id=changes["status_id"])
        old_values = {field: getattr(sprint, field) for field in changes}
        sprint = self.sprints.update(sprint, changes)
        ActivityLogService(self.db).record(
            account_id=sprint.account_id,
            entity_type="SPRINT",
            entity_id=sprint.id,
            action="SPRINT_UPDATED",
            old_values=old_values,
            new_values={field: getattr(sprint, field) for field in changes},
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(sprint)
        return self.enrich_sprint(sprint)

    def resolve_status_id(self, *, account_id: UUID, status_id: UUID | None) -> UUID | None:
        if status_id is None:
            return self.sprints.get_default_status_id(account_id=account_id)
        return self.validate_status_id(account_id=account_id, status_id=status_id)

    def validate_status_id(self, *, account_id: UUID, status_id: UUID) -> UUID:
        status_value = self.sprints.get_valid_status(account_id=account_id, status_id=status_id)
        if status_value is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid sprint status.")
        return status_value.id

    def enrich_sprint(self, sprint: Sprint) -> dict[str, object]:
        return self.enrich_sprints([sprint])[0]

    def enrich_sprints(self, sprints: list[Sprint]) -> list[dict[str, object]]:
        statuses = self.sprints.get_status_values_by_ids({sprint.status_id for sprint in sprints if sprint.status_id is not None})
        return [
            {
                **sprint.__dict__,
                "status": self.status_summary(sprint.status_id, statuses),
            }
            for sprint in sprints
        ]

    def status_summary(self, status_id: UUID | None, statuses: dict[UUID, OptionValue]) -> dict[str, object] | None:
        if status_id is None or status_id not in statuses:
            return None
        status_value = statuses[status_id]
        return {
            "id": status_value.id,
            "label": status_value.label,
            "value": status_value.value,
            "color": status_value.color,
        }

    def get_project_or_404(self, project_id: UUID) -> Project:
        project = self.hierarchy.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        return project

    def get_sprint_or_404(self, sprint_id: UUID) -> Sprint:
        sprint = self.sprints.get(sprint_id)
        if sprint is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sprint not found.")
        return sprint

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")

    def require_account_role(self, *, account_id: UUID, user_id: UUID, allowed_roles: set[str]) -> None:
        self.require_account_member(account_id=account_id, user_id=user_id)
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None or membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")