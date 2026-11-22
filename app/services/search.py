from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import String, and_, bindparam, case, cast, false, func, literal, null, or_, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.account import Account
from app.models.access_grant import ProjectModule
from app.models.account_member import AccountMember
from app.models.assumption import Assumption
from app.models.change_request import ChangeRequest
from app.models.decision import Decision
from app.models.dependency import Dependency
from app.models.issue import Issue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.requirement import Requirement
from app.models.resource import Resource
from app.models.risk import Risk
from app.models.skill import Skill
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.test_case import TestCase
from app.models.user import User
from app.services.access import access_for


SUPPORTED_ENTITY_TYPES: tuple[str, ...] = (
    "PORTFOLIO",
    "PROGRAM",
    "PROJECT",
    "TASK",
    "RISK",
    "ISSUE",
    "ASSUMPTION",
    "DECISION",
    "DEPENDENCY",
    "CHANGE_REQUEST",
    "REQUIREMENT",
    "TEST_CASE",
    "SPRINT",
    "RESOURCE",
    "SKILL",
)
SEARCH_SIMILARITY_THRESHOLD = 0.1


# Search spans every account the user belongs to. What they can see in each comes from their access scope there
# (app.services.access): everything in the account, or the projects, programs and portfolios it lists.
class Visibility:
    def __init__(self, db: Session, user: User) -> None:
        account_ids = db.scalars(select(AccountMember.account_id).where(AccountMember.user_id == user.id)).all()
        self.scopes = [access_for(db, account_id=account_id, user=user) for account_id in account_ids]

    def _within(self, column: ColumnElement[object], model: type, ids_of) -> ColumnElement[bool]:  # type: ignore[no-untyped-def]
        conditions: list[ColumnElement[bool]] = []
        listed: set[UUID] = set()
        for scope in self.scopes:
            ids = ids_of(scope)
            if ids is None:
                conditions.append(column.in_(select(model.id).where(model.account_id == scope.account_id)))
            else:
                listed |= ids
        if listed:
            conditions.append(column.in_(listed))
        return or_(*conditions) if conditions else false()

    def project(self, project_id: ColumnElement[object], module: ProjectModule | None = None) -> ColumnElement[bool]:
        """Projects the user can see; with a module, only where that module is visible too."""
        return self._within(project_id, Project, lambda scope: scope.visible_project_ids(module))

    def program(self, program_id: ColumnElement[object]) -> ColumnElement[bool]:
        return self._within(program_id, Program, lambda scope: scope.visible_program_ids())

    def portfolio(self, portfolio_id: ColumnElement[object]) -> ColumnElement[bool]:
        return self._within(portfolio_id, Portfolio, lambda scope: scope.visible_portfolio_ids())

    def resource(self, resource_id: ColumnElement[object]) -> ColumnElement[bool]:
        """People the user may see on Resources (see AccessScope.listed_resource_ids)."""
        return self._within(resource_id, Resource, lambda scope: scope.listed_resource_ids())

    def resources_area(self, account_id: ColumnElement[object]) -> ColumnElement[bool]:
        """Accounts where the user's role can view Resources (skills live there)."""
        ids = [scope.account_id for scope in self.scopes if scope.allows("RESOURCES", "VIEW")]
        return account_id.in_(ids) if ids else false()


class SearchService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def search(
        self,
        *,
        q: str,
        current_user: User,
        entity_types: str | None = None,
        limit: int = 20,
    ) -> dict[str, list[dict[str, object]]]:
        query = q.strip()
        if len(query) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Search query must be at least 2 characters.",
            )

        requested_entity_types = self.parse_entity_types(entity_types)
        selected_statements = [
            statement
            for entity_type, statement in self.search_statements(current_user).items()
            if entity_type in requested_entity_types
        ]
        if not selected_statements:
            return {"results": []}

        search_results = union_all(*selected_statements).subquery("search_results")
        statement = (
            select(
                search_results.c.entity_type,
                search_results.c.id,
                search_results.c.project_id,
                search_results.c.title,
                search_results.c.subtitle,
                search_results.c.score,
            )
            .order_by(
                search_results.c.exact_match.desc(),
                search_results.c.score.desc(),
                search_results.c.created_at.desc(),
                search_results.c.entity_type,
                search_results.c.id,
            )
            .limit(limit)
        )
        rows = self.db.execute(
            statement,
            {
                "query_lower": query.lower(),
                "pattern": f"%{query}%",
                "user_id": current_user.id,
            },
        ).mappings()

        return {
            "results": [
                {
                    "entity_type": str(row["entity_type"]),
                    "id": self.normalize_id(row["id"]),
                    "project_id": self.normalize_id(row["project_id"]) if row["project_id"] is not None else None,
                    "title": str(row["title"]),
                    "subtitle": str(row["subtitle"]) if row["subtitle"] is not None else None,
                    "score": float(row["score"] or 0),
                }
                for row in rows
            ]
        }

    def parse_entity_types(self, entity_types: str | None) -> set[str]:
        if entity_types is None or not entity_types.strip():
            return set(SUPPORTED_ENTITY_TYPES)

        requested_entity_types = {
            entity_type.strip().upper()
            for entity_type in entity_types.split(",")
            if entity_type.strip()
        }
        invalid_entity_types = requested_entity_types - set(SUPPORTED_ENTITY_TYPES)
        if invalid_entity_types:
            invalid_values = ", ".join(sorted(invalid_entity_types))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid entity type(s): {invalid_values}.",
            )
        return requested_entity_types

    def search_statements(self, current_user: User) -> dict[str, object]:
        user_id_param = bindparam("user_id")
        vis = Visibility(self.db, current_user)
        return {
            "PORTFOLIO": self.entity_statement(
                entity_type="PORTFOLIO",
                id_column=Portfolio.id,
                title_column=Portfolio.name,
                subtitle_column=Account.name,
                created_at_column=Portfolio.created_at,
                from_model=Portfolio,
                joins=(
                    (Account, Account.id == Portfolio.account_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Portfolio.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.portfolio(Portfolio.id),),
            ),
            "PROGRAM": self.entity_statement(
                entity_type="PROGRAM",
                id_column=Program.id,
                title_column=Program.name,
                subtitle_column=Portfolio.name,
                created_at_column=Program.created_at,
                from_model=Program,
                joins=(
                    (Portfolio, Portfolio.id == Program.portfolio_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Program.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.program(Program.id),),
            ),
            "PROJECT": self.entity_statement(
                entity_type="PROJECT",
                project_id_column=Project.id,
                id_column=Project.id,
                title_column=Project.name,
                subtitle_column=Program.name,
                created_at_column=Project.created_at,
                from_model=Project,
                joins=(
                    (Program, Program.id == Project.program_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Project.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Project.id),),
            ),
            "TASK": self.entity_statement(
                entity_type="TASK",
                project_id_column=Task.project_id,
                id_column=Task.id,
                title_column=Task.name,
                subtitle_column=Project.name,
                created_at_column=Task.created_at,
                from_model=Task,
                joins=(
                    (Project, Project.id == Task.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Task.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(Task.is_deleted.is_(False), vis.project(Task.project_id, ProjectModule.TASKS)),
            ),
            "RISK": self.entity_statement(
                entity_type="RISK",
                project_id_column=Risk.project_id,
                id_column=Risk.id,
                title_column=Risk.title,
                subtitle_column=Project.name,
                created_at_column=Risk.created_at,
                from_model=Risk,
                joins=(
                    (Project, Project.id == Risk.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Risk.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Risk.project_id, ProjectModule.RISKS),),
            ),
            "ISSUE": self.entity_statement(
                entity_type="ISSUE",
                project_id_column=Issue.project_id,
                id_column=Issue.id,
                title_column=Issue.title,
                subtitle_column=Project.name,
                created_at_column=Issue.created_at,
                from_model=Issue,
                joins=(
                    (Project, Project.id == Issue.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Issue.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Issue.project_id, ProjectModule.ISSUES),),
            ),
            "ASSUMPTION": self.entity_statement(
                entity_type="ASSUMPTION",
                project_id_column=Assumption.project_id,
                id_column=Assumption.id,
                title_column=Assumption.description,
                subtitle_column=Project.name,
                created_at_column=Assumption.created_at,
                from_model=Assumption,
                joins=(
                    (Project, Project.id == Assumption.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Assumption.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Assumption.project_id, ProjectModule.ASSUMPTIONS),),
            ),
            "DECISION": self.entity_statement(
                entity_type="DECISION",
                project_id_column=Decision.project_id,
                id_column=Decision.id,
                title_column=Decision.decision_text,
                subtitle_column=Project.name,
                created_at_column=Decision.created_at,
                from_model=Decision,
                joins=(
                    (Project, Project.id == Decision.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Decision.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Decision.project_id, ProjectModule.DECISIONS),),
            ),
            "DEPENDENCY": self.entity_statement(
                entity_type="DEPENDENCY",
                project_id_column=Dependency.project_id,
                id_column=Dependency.id,
                title_column=Dependency.title,
                subtitle_column=Project.name,
                created_at_column=Dependency.created_at,
                from_model=Dependency,
                joins=(
                    (Project, Project.id == Dependency.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Dependency.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Dependency.project_id, ProjectModule.DEPENDENCIES),),
            ),
            "CHANGE_REQUEST": self.entity_statement(
                entity_type="CHANGE_REQUEST",
                project_id_column=ChangeRequest.project_id,
                id_column=ChangeRequest.id,
                title_column=ChangeRequest.title,
                subtitle_column=Project.name,
                created_at_column=ChangeRequest.created_at,
                from_model=ChangeRequest,
                joins=(
                    (Project, Project.id == ChangeRequest.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == ChangeRequest.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(ChangeRequest.project_id, ProjectModule.CHANGE_REQUESTS),),
            ),
            "REQUIREMENT": self.entity_statement(
                entity_type="REQUIREMENT",
                project_id_column=Requirement.project_id,
                id_column=Requirement.id,
                title_column=Requirement.title,
                subtitle_column=Project.name,
                created_at_column=Requirement.created_at,
                from_model=Requirement,
                joins=(
                    (Project, Project.id == Requirement.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Requirement.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Requirement.project_id, ProjectModule.REQUIREMENTS),),
            ),
            "TEST_CASE": self.entity_statement(
                entity_type="TEST_CASE",
                project_id_column=TestCase.project_id,
                id_column=TestCase.id,
                title_column=TestCase.title,
                subtitle_column=Project.name,
                created_at_column=TestCase.created_at,
                from_model=TestCase,
                joins=(
                    (Project, Project.id == TestCase.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == TestCase.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(TestCase.project_id, ProjectModule.TESTING),),
            ),
            "SPRINT": self.entity_statement(
                entity_type="SPRINT",
                project_id_column=Sprint.project_id,
                id_column=Sprint.id,
                title_column=Sprint.name,
                extra_search_column=Sprint.goal,
                subtitle_column=Project.name,
                created_at_column=Sprint.created_at,
                from_model=Sprint,
                joins=(
                    (Project, Project.id == Sprint.project_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Sprint.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(vis.project(Sprint.project_id, ProjectModule.SPRINTS),),
            ),
            "RESOURCE": self.entity_statement(
                entity_type="RESOURCE",
                id_column=Resource.id,
                title_column=Resource.name,
                extra_search_column=Resource.role,
                subtitle_column=Account.name,
                created_at_column=Resource.created_at,
                from_model=Resource,
                joins=(
                    (Account, Account.id == Resource.account_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Resource.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(Resource.is_active.is_(True), Resource.listed.is_(True), vis.resource(Resource.id)),
            ),
            "SKILL": self.entity_statement(
                entity_type="SKILL",
                id_column=Skill.id,
                title_column=Skill.name,
                extra_search_column=Skill.category,
                subtitle_column=Account.name,
                created_at_column=Skill.created_at,
                from_model=Skill,
                joins=(
                    (Account, Account.id == Skill.account_id),
                    (
                        AccountMember,
                        and_(
                            AccountMember.account_id == Skill.account_id,
                            AccountMember.user_id == user_id_param,
                        ),
                    ),
                ),
                filters=(Skill.is_active.is_(True), vis.resources_area(Skill.account_id)),
            ),
        }

    def entity_statement(
        self,
        *,
        entity_type: str,
        id_column: ColumnElement[object],
        title_column: ColumnElement[str],
        subtitle_column: ColumnElement[str],
        created_at_column: ColumnElement[object],
        from_model: object,
        joins: tuple[tuple[object, ColumnElement[bool]], ...],
        extra_search_column: ColumnElement[str] | None = None,
        project_id_column: ColumnElement[object] | None = None,
        filters: tuple[ColumnElement[bool], ...] = (),
    ) -> object:
        query_lower = bindparam("query_lower")
        pattern = bindparam("pattern")
        normalized_title = func.lower(title_column)
        score = func.similarity(normalized_title, query_lower)
        statement = select(
            literal(entity_type).label("entity_type"),
            cast(id_column, String).label("id"),
            (cast(project_id_column, String) if project_id_column is not None else cast(null(), String)).label("project_id"),
            title_column.label("title"),
            subtitle_column.label("subtitle"),
            score.label("score"),
            case((normalized_title == query_lower, 1), else_=0).label("exact_match"),
            created_at_column.label("created_at"),
        ).select_from(from_model)
        for target, on_clause in joins:
            statement = statement.join(target, on_clause)
        search_predicates = [title_column.ilike(pattern), score > SEARCH_SIMILARITY_THRESHOLD]
        if extra_search_column is not None:
            search_predicates.append(extra_search_column.ilike(pattern))
            search_predicates.append(func.similarity(func.lower(extra_search_column), query_lower) > SEARCH_SIMILARITY_THRESHOLD)
        return statement.where(*filters, or_(*search_predicates))

    def normalize_id(self, value: object) -> str:
        try:
            return str(UUID(str(value)))
        except ValueError:
            return str(value)