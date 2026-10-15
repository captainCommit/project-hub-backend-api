"""Deleting a portfolio, program or project together with everything under it.

The database removes child rows through ON DELETE CASCADE (programs, projects, tasks, RAID logs, sprints,
access grants...). Comments, attachments and notifications point at records by entity type and id with no
foreign key, so they are removed here. The activity log is kept as the audit trail, plus a DELETED entry.
Files already uploaded to S3 for deleted attachments are left in the bucket.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.assumption import Assumption
from app.models.attachment import Attachment
from app.models.change_request import ChangeRequest
from app.models.comment import Comment
from app.models.decision import Decision
from app.models.decision_option import DecisionOption
from app.models.dependency import Dependency
from app.models.issue import Issue
from app.models.notification import Notification
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.requirement import Requirement
from app.models.risk import Risk
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.test_case import TestCase
from app.models.user import User
from app.repositories.activity import ActivityLogRepository
from app.services.access import access_for

PROJECT_RECORD_MODELS = (Task, Risk, Issue, Assumption, Decision, Dependency, ChangeRequest, Requirement, TestCase, Sprint)


class HierarchyDeleteService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def delete_project(self, *, project_id: UUID, current_user: User) -> None:
        project = self.db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        access_for(self.db, account_id=project.account_id, user=current_user).require_project(project, manage=True)
        self.remove(
            account_id=project.account_id,
            entity_type="PROJECT",
            item=project,
            project_ids=[project.id],
            extra_ids=[],
            current_user=current_user,
        )

    def delete_program(self, *, program_id: UUID, current_user: User) -> None:
        program = self.db.get(Program, program_id)
        if program is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found.")
        access_for(self.db, account_id=program.account_id, user=current_user).require_program(program, manage=True)
        project_ids = list(self.db.scalars(select(Project.id).where(Project.program_id == program.id)).all())
        self.remove(
            account_id=program.account_id,
            entity_type="PROGRAM",
            item=program,
            project_ids=project_ids,
            extra_ids=[],
            current_user=current_user,
        )

    def delete_portfolio(self, *, portfolio_id: UUID, current_user: User) -> None:
        portfolio = self.db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found.")
        access_for(self.db, account_id=portfolio.account_id, user=current_user).require_portfolio(portfolio, manage=True)
        program_ids = list(self.db.scalars(select(Program.id).where(Program.portfolio_id == portfolio.id)).all())
        project_ids = list(self.db.scalars(select(Project.id).where(Project.portfolio_id == portfolio.id)).all())
        self.remove(
            account_id=portfolio.account_id,
            entity_type="PORTFOLIO",
            item=portfolio,
            project_ids=project_ids,
            extra_ids=program_ids,
            current_user=current_user,
        )

    def remove(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        item: Portfolio | Program | Project,
        project_ids: list[UUID],
        extra_ids: list[UUID],
        current_user: User,
    ) -> None:
        record_ids = {item.id, *project_ids, *extra_ids}
        if project_ids:
            for model in PROJECT_RECORD_MODELS:
                record_ids.update(self.db.scalars(select(model.id).where(model.project_id.in_(project_ids))).all())
            decision_ids = select(Decision.id).where(Decision.project_id.in_(project_ids))
            record_ids.update(self.db.scalars(select(DecisionOption.id).where(DecisionOption.decision_id.in_(decision_ids))).all())

        ids = list(record_ids)
        comment_ids = list(self.db.scalars(
            select(Comment.id).where(Comment.account_id == account_id, Comment.entity_id.in_(ids))
        ).all())
        self.db.execute(delete(Attachment).where(
            Attachment.account_id == account_id, Attachment.entity_id.in_(ids + comment_ids)
        ))
        self.db.execute(delete(Notification).where(
            Notification.account_id == account_id, Notification.entity_id.in_(ids + comment_ids)
        ))
        if comment_ids:
            self.db.execute(delete(Comment).where(Comment.id.in_(comment_ids)))

        ActivityLogRepository(self.db).create(
            account_id=account_id,
            entity_type=entity_type,
            entity_id=item.id,
            action="DELETED",
            old_values={"name": item.name, "projects_removed": len(project_ids)},
            new_values=None,
            created_by=current_user.id,
        )
        self.db.delete(item)
        self.db.commit()
