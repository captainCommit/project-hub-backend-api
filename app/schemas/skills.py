from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.skill import SkillProficiency


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=255)


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class SkillRead(BaseModel):
    id: UUID
    account_id: UUID
    name: str
    category: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResourceSkillCreate(BaseModel):
    skill_id: UUID
    proficiency: SkillProficiency


class ResourceSkillUpdate(BaseModel):
    skill_id: UUID | None = None
    proficiency: SkillProficiency | None = None


class ResourceSkillRead(BaseModel):
    id: UUID
    account_id: UUID
    resource_id: UUID
    skill_id: UUID
    proficiency: SkillProficiency
    created_at: datetime
    updated_at: datetime
    skill: SkillRead


class TaskRequiredSkillCreate(BaseModel):
    skill_id: UUID
    required_proficiency: SkillProficiency | None = None


class TaskRequiredSkillUpdate(BaseModel):
    skill_id: UUID | None = None
    required_proficiency: SkillProficiency | None = None


class TaskRequiredSkillRead(BaseModel):
    id: UUID
    account_id: UUID
    task_id: UUID
    skill_id: UUID
    required_proficiency: SkillProficiency | None
    created_at: datetime
    updated_at: datetime
    skill: SkillRead