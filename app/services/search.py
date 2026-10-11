from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import String, and_, bindparam, case, cast, func, literal, or_, select, true, union_all
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models.account import Account
from app.models.access_grant import AccessGrant, GrantRole, ProjectModule
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.assumption import Assumption
from app.models.decision import Decision
from app.models.issue import Issue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.resource import Resource
from app.models.risk import Risk
from app.models.skill import Skill
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.user import User


SUPPORTED_ENTITY_TYPES: tuple[str, ...] = (
    "PORTFOLIO",
    "PROGRAM",
    "PROJECT",
    "TASK",
    "RISK",
    "ISSUE",
    "ASSUMPTION",
    "DECISION",
    "SPRINT",
    "RESOURCE",
    "SKILL",
)
SEARCH_SIMILARITY_THRESHOLD = 0.1


# Search spans every account the user belongs to, so visibility is expressed in SQL: owners see the
# whole account, everyone else what their access grants cover (see app.services.access).
def is_account_owner() -> ColumnElement[bool]:
    return AccountMember.role == AccountMemberRole.OWNER.value


def held_by_user() -> ColumnElement[bool]:
    return AccessGrant.user_id == bindparam("user_id")


def grant_shows(module: ProjectModule) -> ColumnElement[bool]:
    """A grant shows a module when it's a manager grant, has no module limit, or lists the module."""
    return or_(
        AccessGrant.role == GrantRole.MANAGER.value,
        # "No limit" is SQL NULL on older rows and JSON null on rows saved since.
        AccessGrant.view_modules.is_(None),
        cast(AccessGrant.view_modules, String) == "null",
        cast(AccessGrant.view_modules, String).like(f'%"{module.value}"%'),
    )


def visible_project(project_id: ColumnElement[object], module: ProjectModule | None = None) -> ColumnElement[bool]:
    """Projects the user can see; with a module, only those where a grant also shows that module."""
    project = aliased(Project)
    granted_projects = select(project.id).join(
        AccessGrant,
        and_(
            held_by_user(),
            grant_shows(module) if module is not None else true(),
            or_(
                AccessGrant.project_id == project.id,
                AccessGrant.program_id == project.program_id,
                AccessGrant.portfolio_id == project.portfolio_id,
            ),
        ),
    )
    return or_(is_account_owner(), project_id.in_(granted_projects))


def visible_program(program_id: ColumnElement[object]) -> ColumnElement[bool]:
    program = aliased(Program)
    project = aliased(Project)
    program_wide = select(program.id).join(
        AccessGrant,
        and_(held_by_user(), or_(AccessGrant.program_id == program.id, AccessGrant.portfolio_id == program.portfolio_id)),
    )
    parents_of_projects = select(project.program_id).join(
        AccessGrant, and_(held_by_user(), AccessGrant.project_id == project.id)
    )
    return or_(is_account_owner(), program_id.in_(program_wide), program_id.in_(parents_of_projects))


def visible_portfolio(portfolio_id: ColumnElement[object]) -> ColumnElement[bool]:
    program = aliased(Program)
    project = aliased(Project)
    direct = select(AccessGrant.portfolio_id).where(held_by_user(), AccessGrant.portfolio_id.is_not(None))
    via_programs = select(program.portfolio_id).join(
        AccessGrant, and_(held_by_user(), AccessGrant.program_id == program.id)
    )
    via_projects = select(project.portfolio_id).join(
        AccessGrant, and_(held_by_user(), AccessGrant.project_id == project.id)
    )
    return or_(
        is_account_owner(),
        portfolio_id.in_(direct),
        portfolio_id.in_(via_programs),
        portfolio_id.in_(via_projects),
    )


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
            for entity_type, statement in self.search_statements(current_user.id).items()
            if entity_type in requested_entity_types
        ]
        if not selected_statements:
            return {"results": []}

        search_results = union_all(*selected_statements).subquery("search_results")
        statement = (
            select(
                search_results.c.entity_type,
                search_results.c.id,
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

    def search_statements(self, user_id: UUID) -> dict[str, object]:
        user_id_param = bindparam("user_id")
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
                filters=(visible_portfolio(Portfolio.id),),
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
                filters=(visible_program(Program.id),),
            ),
            "PROJECT": self.entity_statement(
                entity_type="PROJECT",
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
                filters=(visible_project(Project.id),),
            ),
            "TASK": self.entity_statement(
                entity_type="TASK",
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
                filters=(Task.is_deleted.is_(False), visible_project(Task.project_id, ProjectModule.TASKS)),
            ),
            "RISK": self.entity_statement(
                entity_type="RISK",
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
                filters=(visible_project(Risk.project_id, ProjectModule.RISKS),),
            ),
            "ISSUE": self.entity_statement(
                entity_type="ISSUE",
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
                filters=(visible_project(Issue.project_id, ProjectModule.ISSUES),),
            ),
            "ASSUMPTION": self.entity_statement(
                entity_type="ASSUMPTION",
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
                filters=(visible_project(Assumption.project_id, ProjectModule.ASSUMPTIONS),),
            ),
            "DECISION": self.entity_statement(
                entity_type="DECISION",
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
                filters=(visible_project(Decision.project_id, ProjectModule.DECISIONS),),
            ),
            "SPRINT": self.entity_statement(
                entity_type="SPRINT",
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
                filters=(visible_project(Sprint.project_id, ProjectModule.SPRINTS),),
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
                filters=(Resource.is_active.is_(True),),
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
                filters=(Skill.is_active.is_(True),),
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
        filters: tuple[ColumnElement[bool], ...] = (),
    ) -> object:
        query_lower = bindparam("query_lower")
        pattern = bindparam("pattern")
        normalized_title = func.lower(title_column)
        score = func.similarity(normalized_title, query_lower)
        statement = select(
            literal(entity_type).label("entity_type"),
            cast(id_column, String).label("id"),
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