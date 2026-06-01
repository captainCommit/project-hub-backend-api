from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.resource_skill import ResourceSkill
from app.models.skill import Skill
from app.models.task_required_skill import TaskRequiredSkill


class SkillRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_skill(self, **values: object) -> Skill:
        skill = Skill(**values)
        self.db.add(skill)
        self.db.flush()
        self.db.refresh(skill)
        return skill

    def get_skill(self, skill_id: UUID) -> Skill | None:
        return self.db.get(Skill, skill_id)

    def list_skills_for_account(self, account_id: UUID, *, include_inactive: bool = False) -> list[Skill]:
        statement = select(Skill).where(Skill.account_id == account_id)
        if not include_inactive:
            statement = statement.where(Skill.is_active.is_(True))
        statement = statement.order_by(Skill.name, Skill.id)
        return list(self.db.scalars(statement).all())

    def update_skill(self, skill: Skill, changes: dict[str, object]) -> Skill:
        for field, value in changes.items():
            setattr(skill, field, value)
        self.db.add(skill)
        self.db.flush()
        self.db.refresh(skill)
        return skill

    def deactivate_skill(self, skill: Skill) -> Skill:
        skill.is_active = False
        self.db.add(skill)
        self.db.flush()
        self.db.refresh(skill)
        return skill

    def create_resource_skill(self, **values: object) -> ResourceSkill:
        resource_skill = ResourceSkill(**values)
        self.db.add(resource_skill)
        self.db.flush()
        self.db.refresh(resource_skill)
        return resource_skill

    def get_resource_skill(self, resource_skill_id: UUID) -> ResourceSkill | None:
        return self.db.get(ResourceSkill, resource_skill_id)

    def list_resource_skills(self, resource_id: UUID) -> list[tuple[ResourceSkill, Skill]]:
        statement: Select[tuple[ResourceSkill, Skill]] = (
            select(ResourceSkill, Skill)
            .join(Skill, Skill.id == ResourceSkill.skill_id)
            .where(ResourceSkill.resource_id == resource_id)
            .order_by(Skill.name, ResourceSkill.id)
        )
        return list(self.db.execute(statement).all())

    def list_resource_skills_for_resources(self, resource_ids: Iterable[UUID]) -> dict[UUID, list[tuple[ResourceSkill, Skill]]]:
        resource_ids = list(resource_ids)
        if not resource_ids:
            return {}
        statement: Select[tuple[ResourceSkill, Skill]] = (
            select(ResourceSkill, Skill)
            .join(Skill, Skill.id == ResourceSkill.skill_id)
            .where(ResourceSkill.resource_id.in_(resource_ids))
            .order_by(ResourceSkill.resource_id, Skill.name, ResourceSkill.id)
        )
        skills_by_resource: dict[UUID, list[tuple[ResourceSkill, Skill]]] = {}
        for resource_skill, skill in self.db.execute(statement).all():
            skills_by_resource.setdefault(resource_skill.resource_id, []).append((resource_skill, skill))
        return skills_by_resource

    def get_resource_skill_for_resource(self, *, resource_id: UUID, skill_id: UUID) -> ResourceSkill | None:
        statement = select(ResourceSkill).where(
            ResourceSkill.resource_id == resource_id,
            ResourceSkill.skill_id == skill_id,
        )
        return self.db.scalar(statement)

    def update_resource_skill(self, resource_skill: ResourceSkill, changes: dict[str, object]) -> ResourceSkill:
        for field, value in changes.items():
            setattr(resource_skill, field, value)
        self.db.add(resource_skill)
        self.db.flush()
        self.db.refresh(resource_skill)
        return resource_skill

    def delete_resource_skill(self, resource_skill: ResourceSkill) -> None:
        self.db.delete(resource_skill)
        self.db.flush()

    def create_task_required_skill(self, **values: object) -> TaskRequiredSkill:
        required_skill = TaskRequiredSkill(**values)
        self.db.add(required_skill)
        self.db.flush()
        self.db.refresh(required_skill)
        return required_skill

    def get_task_required_skill(self, required_skill_id: UUID) -> TaskRequiredSkill | None:
        return self.db.get(TaskRequiredSkill, required_skill_id)

    def list_task_required_skills(self, task_id: UUID) -> list[tuple[TaskRequiredSkill, Skill]]:
        statement: Select[tuple[TaskRequiredSkill, Skill]] = (
            select(TaskRequiredSkill, Skill)
            .join(Skill, Skill.id == TaskRequiredSkill.skill_id)
            .where(TaskRequiredSkill.task_id == task_id)
            .order_by(Skill.name, TaskRequiredSkill.id)
        )
        return list(self.db.execute(statement).all())

    def list_task_required_skills_for_tasks(self, task_ids: Iterable[UUID]) -> dict[UUID, list[tuple[TaskRequiredSkill, Skill]]]:
        task_ids = list(task_ids)
        if not task_ids:
            return {}
        statement: Select[tuple[TaskRequiredSkill, Skill]] = (
            select(TaskRequiredSkill, Skill)
            .join(Skill, Skill.id == TaskRequiredSkill.skill_id)
            .where(TaskRequiredSkill.task_id.in_(task_ids))
            .order_by(TaskRequiredSkill.task_id, Skill.name, TaskRequiredSkill.id)
        )
        skills_by_task: dict[UUID, list[tuple[TaskRequiredSkill, Skill]]] = {}
        for required_skill, skill in self.db.execute(statement).all():
            skills_by_task.setdefault(required_skill.task_id, []).append((required_skill, skill))
        return skills_by_task

    def get_task_required_skill_for_task(self, *, task_id: UUID, skill_id: UUID) -> TaskRequiredSkill | None:
        statement = select(TaskRequiredSkill).where(
            TaskRequiredSkill.task_id == task_id,
            TaskRequiredSkill.skill_id == skill_id,
        )
        return self.db.scalar(statement)

    def update_task_required_skill(self, required_skill: TaskRequiredSkill, changes: dict[str, object]) -> TaskRequiredSkill:
        for field, value in changes.items():
            setattr(required_skill, field, value)
        self.db.add(required_skill)
        self.db.flush()
        self.db.refresh(required_skill)
        return required_skill

    def delete_task_required_skill(self, required_skill: TaskRequiredSkill) -> None:
        self.db.delete(required_skill)
        self.db.flush()