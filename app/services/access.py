"""Who can see and change what inside an account.

Owners can do everything. Everyone else works through access grants: a MANAGER, MEMBER or VIEWER
role on a portfolio, program or project, which covers everything beneath it. Where several grants
apply, the strongest role wins, and a MEMBER may edit the union of the modules their grants allow.
A grant never lifts anyone above their account role, so a viewer seat stays read-only.

Members and viewers can also be limited to some modules: a grant's view_modules lists the parts of a
project they may see (None means all of them). Several grants see the union of what each allows.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.models.access_grant import ALL_MODULES, AccessGrant, ProjectModule
from app.models.account_member import AccountMemberRole
from app.models.assumption import Assumption
from app.models.billing import AccountBilling
from app.models.change_request import ChangeRequest
from app.models.comment import Comment
from app.models.decision import Decision
from app.models.decision_option import DecisionOption
from app.models.dependency import Dependency
from app.models.issue import Issue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.requirement import Requirement
from app.models.risk import Risk
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.test_case import TestCase
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository


ROLE_RANK = {
    AccountMemberRole.VIEWER.value: 1,
    AccountMemberRole.MEMBER.value: 2,
    AccountMemberRole.MANAGER.value: 3,
    AccountMemberRole.OWNER.value: 4,
}
RANK_ROLE = {rank: role for role, rank in ROLE_RANK.items()}

PROJECT_ACCESS_DENIED = "Project access denied."
ACCOUNT_READ_ONLY = (
    "This account is read-only until its billing is sorted out. An account owner can fix this under Account, Billing."
)
INSUFFICIENT_PROJECT_PERMISSION = "Insufficient project permission."
MODULE_HIDDEN = "You don't have access to this part of the project."

_CACHE_KEY = "access_scopes"

HIERARCHY_ENTITIES = {"PORTFOLIO", "PROGRAM", "PROJECT"}
COMMENTABLE_MODELS: dict[str, type[Any]] = {
    "PORTFOLIO": Portfolio,
    "PROGRAM": Program,
    "PROJECT": Project,
    "TASK": Task,
    "SPRINT": Sprint,
    "RISK": Risk,
    "ISSUE": Issue,
    "ASSUMPTION": Assumption,
    "DECISION": Decision,
    "DECISION_OPTION": DecisionOption,
    "DEPENDENCY": Dependency,
    "CHANGE_REQUEST": ChangeRequest,
    "REQUIREMENT": Requirement,
    "TEST_CASE": TestCase,
}
# The module whose edit right lets someone change, comment on or attach to a record of each type.
ENTITY_MODULES = {
    "TASK": ProjectModule.TASKS,
    "SPRINT": ProjectModule.SPRINTS,
    "RISK": ProjectModule.RISKS,
    "ISSUE": ProjectModule.ISSUES,
    "ASSUMPTION": ProjectModule.ASSUMPTIONS,
    "DECISION": ProjectModule.DECISIONS,
    "DEPENDENCY": ProjectModule.DEPENDENCIES,
    "CHANGE_REQUEST": ProjectModule.CHANGE_REQUESTS,
    "REQUIREMENT": ProjectModule.REQUIREMENTS,
    "TEST_CASE": ProjectModule.TESTING,
}


def _module_value(module: ProjectModule | str) -> str:
    return module.value if isinstance(module, ProjectModule) else module


@dataclass(frozen=True)
class Permission:
    role: str
    # Modules a MEMBER may edit.
    modules: frozenset[str]
    # Modules that are visible at all.
    visible: frozenset[str] = frozenset(ALL_MODULES)

    @property
    def can_manage(self) -> bool:
        return self.role in (AccountMemberRole.OWNER.value, AccountMemberRole.MANAGER.value)

    def can_view(self, module: ProjectModule | str) -> bool:
        return self.can_manage or _module_value(module) in self.visible

    def can_edit(self, module: ProjectModule | str) -> bool:
        if self.can_manage:
            return True
        value = _module_value(module)
        return self.role == AccountMemberRole.MEMBER.value and value in self.modules and value in self.visible

    @property
    def editable_modules(self) -> list[str]:
        return [module for module in ALL_MODULES if self.can_edit(module)]

    @property
    def visible_modules(self) -> list[str]:
        return [module for module in ALL_MODULES if self.can_view(module)]


OWNER_PERMISSION = Permission(AccountMemberRole.OWNER.value, frozenset(ALL_MODULES))


class AccessScope:
    """One user's access within one account. Get it with access_for(); it is loaded once per session."""

    def __init__(self, db: Session, *, account_id: UUID, user: User) -> None:
        if AccountRepository(db).get_by_id(account_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = AccountMemberRepository(db).get_for_user(account_id=account_id, user_id=user.id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")
        self.db = db
        self.account_id = account_id
        self.user = user
        self.account_role = membership.role
        self.is_owner = membership.role == AccountMemberRole.OWNER.value
        billing = db.get(AccountBilling, account_id)
        self.read_only = bool(billing and billing.is_read_only)

        grants = db.scalars(
            select(AccessGrant).where(AccessGrant.account_id == account_id, AccessGrant.user_id == user.id)
        ).all()
        self.portfolio_grants = {g.portfolio_id: g for g in grants if g.portfolio_id is not None}
        self.program_grants = {g.program_id: g for g in grants if g.program_id is not None}
        self.project_grants = {g.project_id: g for g in grants if g.project_id is not None}
        self._visible_project_ids: set[UUID] | None = None
        self._program_scope_ids: set[UUID] | None = None
        self._module_project_ids: dict[str, set[UUID]] = {}
        self._module_program_ids: dict[str, set[UUID]] = {}

    # Permissions on one item

    def combine(self, grants: list[AccessGrant | None]) -> Permission | None:
        applicable = [grant for grant in grants if grant is not None]
        if not applicable:
            return None
        best = max(ROLE_RANK[grant.role] for grant in applicable)
        role = RANK_ROLE[min(best, ROLE_RANK[self.account_role])]
        if best == ROLE_RANK[AccountMemberRole.MANAGER.value]:
            # A manager grant held by someone whose seat was since lowered keeps every module it covered.
            return Permission(role, frozenset(ALL_MODULES))
        modules = frozenset(
            module
            for grant in applicable
            if grant.role == AccountMemberRole.MEMBER.value
            for module in (grant.modules or [])
        )
        visible: set[str] = set()
        for grant in applicable:
            visible.update(ALL_MODULES if grant.view_modules is None else grant.view_modules)
        return Permission(role, modules, frozenset(visible))

    def for_project(self, project: Project) -> Permission | None:
        if self.is_owner:
            return OWNER_PERMISSION
        return self.project_permission(project.portfolio_id, project.program_id, project.id)

    def project_permission(self, portfolio_id: UUID, program_id: UUID, project_id: UUID) -> Permission | None:
        return self.combine(
            [
                self.portfolio_grants.get(portfolio_id),
                self.program_grants.get(program_id),
                self.project_grants.get(project_id),
            ]
        )

    def for_program(self, program: Program) -> Permission | None:
        """Program-wide permission: from grants on the program or its portfolio, not on one of its projects."""
        if self.is_owner:
            return OWNER_PERMISSION
        return self.combine([self.portfolio_grants.get(program.portfolio_id), self.program_grants.get(program.id)])

    def for_portfolio(self, portfolio: Portfolio) -> Permission | None:
        if self.is_owner:
            return OWNER_PERMISSION
        return self.combine([self.portfolio_grants.get(portfolio.id)])

    # Checks that raise

    def require(
        self,
        permission: Permission | None,
        *,
        module: ProjectModule | None,
        manage: bool,
        view: ProjectModule | None = None,
    ) -> Permission:
        """module: edit rights on it; view: just seeing it; neither: seeing the item itself."""
        if permission is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)
        if view is not None and not permission.can_view(view):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MODULE_HIDDEN)
        if (manage and not permission.can_manage) or (module is not None and not permission.can_edit(module)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=INSUFFICIENT_PROJECT_PERMISSION)
        if manage or module is not None:
            self.require_writable()
        return permission

    def require_writable(self) -> None:
        """Refuse changes while the account is locked for non-payment. Reading still works."""
        if self.read_only:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=ACCOUNT_READ_ONLY)

    def require_project(
        self,
        project: Project,
        *,
        module: ProjectModule | None = None,
        manage: bool = False,
        view: ProjectModule | None = None,
    ) -> Permission:
        """View access by default; edit access to `module`, sight of `view`, or manager access with manage=True."""
        return self.require(self.for_project(project), module=module, manage=manage, view=view)

    def require_program(
        self,
        program: Program,
        *,
        module: ProjectModule | None = None,
        manage: bool = False,
        view: ProjectModule | None = None,
    ) -> Permission:
        return self.require(self.for_program(program), module=module, manage=manage, view=view)

    def require_portfolio(self, portfolio: Portfolio, *, manage: bool = False) -> Permission:
        return self.require(self.for_portfolio(portfolio), module=None, manage=manage)

    def require_item(
        self,
        *,
        project_id: UUID | None,
        program_id: UUID | None,
        module: ProjectModule | None = None,
        manage: bool = False,
        view: ProjectModule | None = None,
    ) -> Permission:
        """For records that hang off a project, or off a program when they have no project."""
        if self.is_owner:
            if module is not None or manage:
                self.require_writable()
            return OWNER_PERMISSION
        if project_id is not None:
            project = self.db.get(Project, project_id)
            if project is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
            return self.require_project(project, module=module, manage=manage, view=view)
        program = self.db.get(Program, program_id) if program_id is not None else None
        if program is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)
        return self.require_program(program, module=module, manage=manage, view=view)

    def require_entity(self, entity_type: str, record: Any, *, contribute: bool = False) -> Permission | None:
        """Access to any record that comments, attachments and activity hang off.

        contribute=False needs view access; True needs edit access to the record's module
        (or, on a portfolio, program or project itself, any role above viewer).
        """
        if self.is_owner:
            if contribute:
                self.require_writable()
            return OWNER_PERMISSION
        if entity_type in HIERARCHY_ENTITIES:
            if not contribute:
                if entity_type == "PORTFOLIO":
                    self.require_visible_portfolio(record)
                elif entity_type == "PROGRAM":
                    self.require_visible_program(record)
                else:
                    self.require_project(record)
                return None
            permission = (
                self.for_portfolio(record)
                if entity_type == "PORTFOLIO"
                else self.for_program(record)
                if entity_type == "PROGRAM"
                else self.for_project(record)
            )
            permission = self.require(permission, module=None, manage=False)
            if permission.role == AccountMemberRole.VIEWER.value:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=INSUFFICIENT_PROJECT_PERMISSION)
            self.require_writable()
            return permission
        if entity_type == "COMMENT":
            # A comment (and anything attached to it) belongs to whatever it was left on.
            target_model = COMMENTABLE_MODELS.get(record.entity_type)
            target = self.db.get(target_model, record.entity_id) if target_model else None
            if target is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)
            return self.require_entity(record.entity_type, target, contribute=contribute)
        if entity_type == "DECISION_OPTION":
            entity_type, record = "DECISION", self.db.get(Decision, record.decision_id)
        module = ENTITY_MODULES.get(entity_type)
        if module is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)
        return self.require_item(
            project_id=record.project_id,
            program_id=getattr(record, "program_id", None),
            module=module if contribute else None,
            view=module,
        )

    # What is visible, for list queries. None means everything in the account (owners).

    def visible_project_ids(self, module: ProjectModule | None = None) -> set[UUID] | None:
        """Projects the user can see; with a module, only those where that module is visible too."""
        if self.is_owner:
            return None
        if module is not None:
            return self.module_project_ids(module)
        if self._visible_project_ids is None:
            self._visible_project_ids = set(
                self.db.scalars(
                    select(Project.id).where(
                        Project.account_id == self.account_id,
                        or_(
                            Project.portfolio_id.in_(self.portfolio_grants),
                            Project.program_id.in_(self.program_grants),
                            Project.id.in_(self.project_grants),
                        ),
                    )
                ).all()
            )
        return self._visible_project_ids

    def module_project_ids(self, module: ProjectModule) -> set[UUID]:
        key = _module_value(module)
        if key not in self._module_project_ids:
            rows = self.db.execute(
                select(Project.id, Project.portfolio_id, Project.program_id).where(
                    Project.id.in_(self.visible_project_ids() or set())
                )
            ).all()
            self._module_project_ids[key] = {
                project_id
                for project_id, portfolio_id, program_id in rows
                if (permission := self.project_permission(portfolio_id, program_id, project_id)) is not None
                and permission.can_view(key)
            }
        return self._module_project_ids[key]

    def program_scope_ids(self, module: ProjectModule | None = None) -> set[UUID] | None:
        """Programs this user holds a program-wide grant over (directly or through the portfolio).

        With a module, only programs where that module is visible.
        """
        if self.is_owner:
            return None
        if module is not None:
            key = _module_value(module)
            if key not in self._module_program_ids:
                rows = self.db.execute(
                    select(Program.id, Program.portfolio_id).where(Program.id.in_(self.program_scope_ids() or set()))
                ).all()
                self._module_program_ids[key] = {
                    program_id
                    for program_id, portfolio_id in rows
                    if (
                        permission := self.combine(
                            [self.portfolio_grants.get(portfolio_id), self.program_grants.get(program_id)]
                        )
                    )
                    is not None
                    and permission.can_view(key)
                }
            return self._module_program_ids[key]
        if self._program_scope_ids is None:
            self._program_scope_ids = set(
                self.db.scalars(
                    select(Program.id).where(
                        Program.account_id == self.account_id,
                        or_(Program.portfolio_id.in_(self.portfolio_grants), Program.id.in_(self.program_grants)),
                    )
                ).all()
            )
        return self._program_scope_ids

    def visible_program_ids(self) -> set[UUID] | None:
        """Programs shown in navigation: program-wide grants, plus the parents of visible projects."""
        if self.is_owner:
            return None
        project_ids = self.visible_project_ids() or set()
        parents = set(self.db.scalars(select(Project.program_id).where(Project.id.in_(project_ids))).all())
        return (self.program_scope_ids() or set()) | parents

    def visible_portfolio_ids(self) -> set[UUID] | None:
        if self.is_owner:
            return None
        program_ids = self.visible_program_ids() or set()
        parents = set(self.db.scalars(select(Program.portfolio_id).where(Program.id.in_(program_ids))).all())
        return set(self.portfolio_grants) | parents

    def require_visible_portfolio(self, portfolio: Portfolio) -> None:
        """For navigation: a portfolio is visible when the user can see anything inside it."""
        visible = self.visible_portfolio_ids()
        if visible is not None and portfolio.id not in visible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)

    def require_visible_program(self, program: Program) -> None:
        visible = self.visible_program_ids()
        if visible is not None and program.id not in visible:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)

    def require_visible_filters(
        self,
        *,
        portfolio_id: UUID | None = None,
        program_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> None:
        """Reject an explicit portfolio/program/project filter naming something this user cannot see."""
        for wanted, visible in (
            (portfolio_id, self.visible_portfolio_ids),
            (program_id, self.visible_program_ids),
            (project_id, self.visible_project_ids),
        ):
            if wanted is None:
                continue
            ids = visible()
            if ids is not None and wanted not in ids:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)

    def can_view_activity(self, entity_type: str, entity_id: UUID) -> bool:
        """Whether an activity entry is about a record in a module this user can see."""
        if self.is_owner:
            return True
        entity_type = entity_type.upper()
        if entity_type == "DECISION_OPTION":
            option = self.db.get(DecisionOption, entity_id)
            entity_type, entity_id = "DECISION", option.decision_id if option else entity_id
        if entity_type not in ENTITY_MODULES:
            return True
        record = self.db.get(COMMENTABLE_MODELS[entity_type], entity_id)
        if record is None:
            return False
        try:
            self.require_entity(entity_type, record)
        except HTTPException:
            return False
        return True

    def keep_visible(self, items: list, visible_ids: set[UUID] | None) -> list:
        return items if visible_ids is None else [item for item in items if item.id in visible_ids]

    def project_filter(
        self, project_column: ColumnElement[UUID], module: ProjectModule | None = None
    ) -> ColumnElement[bool] | None:
        """A WHERE clause limiting rows to visible projects (where `module` is visible), or None for no filter."""
        project_ids = self.visible_project_ids(module)
        if project_ids is None:
            return None
        return project_column.in_(project_ids) if project_ids else false()

    def item_filter(
        self,
        project_column: ColumnElement[UUID],
        program_column: ColumnElement[UUID],
        module: ProjectModule | None = None,
    ) -> ColumnElement[bool] | None:
        """Like project_filter, also letting through program-level rows (no project) the user can see."""
        project_ids = self.visible_project_ids(module)
        if project_ids is None:
            return None
        program_ids = self.program_scope_ids(module) or set()
        return or_(
            project_column.in_(project_ids) if project_ids else false(),
            and_(project_column.is_(None), program_column.in_(program_ids)) if program_ids else false(),
        )


def hidden_entity_types(permission: Permission) -> set[str]:
    """Activity entity types belonging to modules this permission can't see."""
    hidden = {entity for entity, module in ENTITY_MODULES.items() if not permission.can_view(module)}
    if "DECISION" in hidden:
        hidden.add("DECISION_OPTION")
    if "TASK" in hidden:
        hidden.add("RESOURCE_ALLOCATION")
    return hidden


def access_for(db: Session, *, account_id: UUID, user: User) -> AccessScope:
    """The user's access in this account, loaded once per request."""
    request_id = get_request_id()
    cached = db.info.get(_CACHE_KEY)
    if cached is None or cached[0] != request_id:
        cached = (request_id, {})
        db.info[_CACHE_KEY] = cached
    scopes: dict[tuple[UUID, UUID], AccessScope] = cached[1]
    key = (account_id, user.id)
    if key not in scopes or request_id is None:
        scopes[key] = AccessScope(db, account_id=account_id, user=user)
    return scopes[key]


def forget_access(db: Session) -> None:
    """Drop cached access after grants or roles change."""
    db.info.pop(_CACHE_KEY, None)


def can_see_entity(db: Session, *, account_id: UUID, user: User, entity_type: str, entity_id: UUID) -> bool:
    """Whether a user may view a record, for deciding who hears about it (e.g. notifications)."""
    entity_type = entity_type.upper()
    model = Comment if entity_type == "COMMENT" else COMMENTABLE_MODELS.get(entity_type)
    record = db.get(model, entity_id) if model is not None else None
    if record is None:
        return False
    try:
        AccessScope(db, account_id=account_id, user=user).require_entity(entity_type, record)
    except HTTPException:
        return False
    return True
