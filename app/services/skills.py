from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from app.models.resource import Resource
from app.models.resource_skill import ResourceSkill
from app.models.skill import Skill
from app.models.task import Task
from app.models.task_required_skill import TaskRequiredSkill
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.resources import ResourceRepository
from app.repositories.skills import SkillRepository
from app.repositories.tasks import TaskRepository
from app.schemas.skills import (
    ResourceSkillCreate,
    ResourceSkillUpdate,
    SkillCreate,
    SkillUpdate,
    TaskRequiredSkillCreate,
    TaskRequiredSkillUpdate,
)


SKILL_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
}
SKILL_ASSIGNMENT_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}


class SkillService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.resources = ResourceRepository(db)
        self.skills = SkillRepository(db)
        self.tasks = TaskRepository(db)

    def list_skills(self, *, account_id: UUID, current_user: User, include_inactive: bool = False) -> list[Skill]:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        return self.skills.list_skills_for_account(account_id, include_inactive=include_inactive)

    def create_skill(self, *, account_id: UUID, skill_in: SkillCreate, current_user: User) -> Skill:
        self.require_account_role(account_id=account_id, user_id=current_user.id, allowed_roles=SKILL_WRITE_ROLES)
        skill = self.skills.create_skill(
            account_id=account_id,
            name=skill_in.name,
            category=skill_in.category,
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def get_skill(self, *, skill_id: UUID, current_user: User) -> Skill:
        skill = self.get_skill_or_404(skill_id)
        self.require_account_member(account_id=skill.account_id, user_id=current_user.id)
        return skill

    def update_skill(self, *, skill_id: UUID, skill_in: SkillUpdate, current_user: User) -> Skill:
        skill = self.get_skill_or_404(skill_id)
        self.require_account_role(account_id=skill.account_id, user_id=current_user.id, allowed_roles=SKILL_WRITE_ROLES)
        changes = skill_in.model_dump(exclude_unset=True)
        if not changes:
            return skill
        skill = self.skills.update_skill(skill, changes)
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def delete_skill(self, *, skill_id: UUID, current_user: User) -> None:
        skill = self.get_skill_or_404(skill_id)
        self.require_account_role(account_id=skill.account_id, user_id=current_user.id, allowed_roles=SKILL_WRITE_ROLES)
        if not skill.is_active:
            return
        self.skills.deactivate_skill(skill)
        self.db.commit()

    def list_resource_skills(self, *, resource_id: UUID, current_user: User) -> list[dict[str, object]]:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_member(account_id=resource.account_id, user_id=current_user.id)
        return [self.resource_skill_summary(resource_skill, skill) for resource_skill, skill in self.skills.list_resource_skills(resource.id)]

    def create_resource_skill(
        self,
        *,
        resource_id: UUID,
        resource_skill_in: ResourceSkillCreate,
        current_user: User,
    ) -> dict[str, object]:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_role(
            account_id=resource.account_id,
            user_id=current_user.id,
            allowed_roles=SKILL_ASSIGNMENT_WRITE_ROLES,
        )
        skill = self.validate_active_skill_for_account(skill_id=resource_skill_in.skill_id, account_id=resource.account_id)
        if self.skills.get_resource_skill_for_resource(resource_id=resource.id, skill_id=skill.id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Resource skill already exists.")
        try:
            resource_skill = self.skills.create_resource_skill(
                account_id=resource.account_id,
                resource_id=resource.id,
                skill_id=skill.id,
                proficiency=resource_skill_in.proficiency.value,
            )
            self.db.commit()
            self.db.refresh(resource_skill)
            return self.resource_skill_summary(resource_skill, skill)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Resource skill already exists.") from exc

    def update_resource_skill(
        self,
        *,
        resource_skill_id: UUID,
        resource_skill_in: ResourceSkillUpdate,
        current_user: User,
    ) -> dict[str, object]:
        resource_skill = self.get_resource_skill_or_404(resource_skill_id)
        self.require_account_role(
            account_id=resource_skill.account_id,
            user_id=current_user.id,
            allowed_roles=SKILL_ASSIGNMENT_WRITE_ROLES,
        )
        changes = resource_skill_in.model_dump(exclude_unset=True)
        skill = self.get_skill_or_404(resource_skill.skill_id)
        if "skill_id" in changes:
            skill = self.validate_active_skill_for_account(
                skill_id=changes["skill_id"],  # type: ignore[arg-type]
                account_id=resource_skill.account_id,
            )
            duplicate = self.skills.get_resource_skill_for_resource(
                resource_id=resource_skill.resource_id,
                skill_id=skill.id,
            )
            if duplicate is not None and duplicate.id != resource_skill.id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Resource skill already exists.")
            changes["skill_id"] = skill.id
        if "proficiency" in changes:
            changes["proficiency"] = changes["proficiency"].value
        if changes:
            try:
                resource_skill = self.skills.update_resource_skill(resource_skill, changes)
                self.db.commit()
                self.db.refresh(resource_skill)
            except IntegrityError as exc:
                self.db.rollback()
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Resource skill already exists.") from exc
        return self.resource_skill_summary(resource_skill, skill)

    def delete_resource_skill(self, *, resource_skill_id: UUID, current_user: User) -> None:
        resource_skill = self.get_resource_skill_or_404(resource_skill_id)
        self.require_account_role(
            account_id=resource_skill.account_id,
            user_id=current_user.id,
            allowed_roles=SKILL_ASSIGNMENT_WRITE_ROLES,
        )
        self.skills.delete_resource_skill(resource_skill)
        self.db.commit()

    def list_task_required_skills(self, *, task_id: UUID, current_user: User) -> list[dict[str, object]]:
        task = self.get_task_or_404(task_id)
        self.require_account_member(account_id=task.account_id, user_id=current_user.id)
        return [
            self.task_required_skill_summary(required_skill, skill)
            for required_skill, skill in self.skills.list_task_required_skills(task.id)
        ]

    def create_task_required_skill(
        self,
        *,
        task_id: UUID,
        required_skill_in: TaskRequiredSkillCreate,
        current_user: User,
    ) -> dict[str, object]:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=SKILL_ASSIGNMENT_WRITE_ROLES,
        )
        skill = self.validate_active_skill_for_account(skill_id=required_skill_in.skill_id, account_id=task.account_id)
        if self.skills.get_task_required_skill_for_task(task_id=task.id, skill_id=skill.id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task required skill already exists.")
        try:
            required_skill = self.skills.create_task_required_skill(
                account_id=task.account_id,
                task_id=task.id,
                skill_id=skill.id,
                required_proficiency=(
                    required_skill_in.required_proficiency.value
                    if required_skill_in.required_proficiency is not None
                    else None
                ),
            )
            self.db.commit()
            self.db.refresh(required_skill)
            return self.task_required_skill_summary(required_skill, skill)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task required skill already exists.") from exc

    def update_task_required_skill(
        self,
        *,
        required_skill_id: UUID,
        required_skill_in: TaskRequiredSkillUpdate,
        current_user: User,
    ) -> dict[str, object]:
        required_skill = self.get_task_required_skill_or_404(required_skill_id)
        self.require_account_role(
            account_id=required_skill.account_id,
            user_id=current_user.id,
            allowed_roles=SKILL_ASSIGNMENT_WRITE_ROLES,
        )
        changes = required_skill_in.model_dump(exclude_unset=True)
        skill = self.get_skill_or_404(required_skill.skill_id)
        if "skill_id" in changes:
            skill = self.validate_active_skill_for_account(
                skill_id=changes["skill_id"],  # type: ignore[arg-type]
                account_id=required_skill.account_id,
            )
            duplicate = self.skills.get_task_required_skill_for_task(task_id=required_skill.task_id, skill_id=skill.id)
            if duplicate is not None and duplicate.id != required_skill.id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task required skill already exists.")
            changes["skill_id"] = skill.id
        if "required_proficiency" in changes and changes["required_proficiency"] is not None:
            changes["required_proficiency"] = changes["required_proficiency"].value
        if changes:
            try:
                required_skill = self.skills.update_task_required_skill(required_skill, changes)
                self.db.commit()
                self.db.refresh(required_skill)
            except IntegrityError as exc:
                self.db.rollback()
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task required skill already exists.") from exc
        return self.task_required_skill_summary(required_skill, skill)

    def delete_task_required_skill(self, *, required_skill_id: UUID, current_user: User) -> None:
        required_skill = self.get_task_required_skill_or_404(required_skill_id)
        self.require_account_role(
            account_id=required_skill.account_id,
            user_id=current_user.id,
            allowed_roles=SKILL_ASSIGNMENT_WRITE_ROLES,
        )
        self.skills.delete_task_required_skill(required_skill)
        self.db.commit()

    def validate_active_skill_for_account(self, *, skill_id: UUID, account_id: UUID) -> Skill:
        skill = self.get_skill_or_404(skill_id)
        if skill.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill must belong to the account.")
        if not skill.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive skills cannot be assigned.")
        return skill

    def resource_skill_summary(self, resource_skill: ResourceSkill, skill: Skill) -> dict[str, object]:
        return {
            "id": resource_skill.id,
            "account_id": resource_skill.account_id,
            "resource_id": resource_skill.resource_id,
            "skill_id": resource_skill.skill_id,
            "proficiency": resource_skill.proficiency,
            "created_at": resource_skill.created_at,
            "updated_at": resource_skill.updated_at,
            "skill": skill,
        }

    def task_required_skill_summary(self, required_skill: TaskRequiredSkill, skill: Skill) -> dict[str, object]:
        return {
            "id": required_skill.id,
            "account_id": required_skill.account_id,
            "task_id": required_skill.task_id,
            "skill_id": required_skill.skill_id,
            "required_proficiency": required_skill.required_proficiency,
            "created_at": required_skill.created_at,
            "updated_at": required_skill.updated_at,
            "skill": skill,
        }

    def get_skill_or_404(self, skill_id: UUID) -> Skill:
        skill = self.skills.get_skill(skill_id)
        if skill is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found.")
        return skill

    def get_resource_or_404(self, resource_id: UUID) -> Resource:
        resource = self.resources.get_resource(resource_id)
        if resource is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
        return resource

    def get_task_or_404(self, task_id: UUID) -> Task:
        task = self.tasks.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        return task

    def get_resource_skill_or_404(self, resource_skill_id: UUID) -> ResourceSkill:
        resource_skill = self.skills.get_resource_skill(resource_skill_id)
        if resource_skill is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource skill not found.")
        return resource_skill

    def get_task_required_skill_or_404(self, required_skill_id: UUID) -> TaskRequiredSkill:
        required_skill = self.skills.get_task_required_skill(required_skill_id)
        if required_skill is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task required skill not found.")
        return required_skill

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