"""Who can see and change what inside an account.

Each member holds an account role (Account > Users & Roles): rights per area (Portfolios, Programs, Projects,
Requirements, Testing, Sprints, RAID, Governance, Resources, Reporting) for View / Create / Edit / Delete / Approve,
and a scope. A role scoped to ALL reaches every project in the account; an ASSIGNED role reaches the projects the
person is assigned to: an access grant on the project, its program or its portfolio, or a resource allocation to
the project (Resources > Capacity). Owners can do everything.

Access grants are only the record of assignments now; their role and module columns are no longer read.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.models.access_grant import ALL_MODULES, AccessGrant, ProjectModule
from app.models.account_member import AccountMemberRole
from app.models.account_role import SCOPE_ALL, AccountRole
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
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.resource_assignment import ResourceAssignment
from app.models.resource_project_allocation import ResourceProjectAllocation
from app.models.risk import Risk
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.task_assignment import TaskAssignment
from app.models.test_case import TestCase
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.services.role_defaults import DEFAULT_ROLES_BY_KEY


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


# The area of the role grid that governs each project module.
MODULE_AREAS = {
    ProjectModule.TASKS.value: "PROJECTS",
    ProjectModule.FINANCIALS.value: "PROJECTS",
    ProjectModule.SPRINTS.value: "SPRINTS",
    ProjectModule.RISKS.value: "RAID",
    ProjectModule.ISSUES.value: "RAID",
    ProjectModule.ASSUMPTIONS.value: "RAID",
    ProjectModule.DEPENDENCIES.value: "RAID",
    ProjectModule.DECISIONS.value: "GOVERNANCE",
    ProjectModule.CHANGE_REQUESTS.value: "GOVERNANCE",
    ProjectModule.REQUIREMENTS.value: "REQUIREMENTS",
    ProjectModule.TESTING.value: "TESTING",
}
# The area that governs changing (or deleting) a portfolio, program or project itself.
LEVEL_AREAS = {"PORTFOLIO": "PORTFOLIOS", "PROGRAM": "PROGRAMS", "PROJECT": "PROJECTS"}

# Members without a role_id (older data) get the built-in role closest to their seat.
LEGACY_ROLE_KEYS = {
    AccountMemberRole.OWNER.value: "OWNER",
    AccountMemberRole.MANAGER.value: "PROJECT_MANAGER",
    AccountMemberRole.MEMBER.value: "CONTRIBUTOR",
    AccountMemberRole.VIEWER.value: "VIEWER",
}


def seat_of(role: AccountRole) -> str:
    """The seat a role costs: OWNER, VIEWER for view-only roles, MEMBER otherwise (see account_roles.seat_for)."""
    if role.key == "OWNER":
        return AccountMemberRole.OWNER.value
    can_change = any(action != "VIEW" for actions in (role.permissions or {}).values() for action in actions)
    return AccountMemberRole.MEMBER.value if can_change else AccountMemberRole.VIEWER.value


def _module_value(module: ProjectModule | str) -> str:
    return module.value if isinstance(module, ProjectModule) else module


def area_of(module: ProjectModule | str) -> str:
    return MODULE_AREAS[_module_value(module)]


Rights = dict[str, frozenset[str]]


def rights_of(permissions: dict[str, list[str]] | None) -> Rights:
    return {area: frozenset(actions) for area, actions in (permissions or {}).items()}


@dataclass(frozen=True)
class Permission:
    """What one person may do on one portfolio, program or project (`level`), from their account role."""

    rights: Rights = field(default_factory=dict)
    level: str = "PROJECT"
    owner: bool = False

    def allows(self, area: str, action: str = "VIEW") -> bool:
        return self.owner or action in self.rights.get(area, frozenset())

    @property
    def can_manage(self) -> bool:
        """May change the portfolio, program or project itself (its details, who is assigned to it)."""
        return self.allows(LEVEL_AREAS[self.level], "EDIT")

    @property
    def can_contribute(self) -> bool:
        """Has any right beyond viewing (comments and attachments need this)."""
        return self.owner or any(actions - {"VIEW"} for actions in self.rights.values())

    @property
    def role(self) -> str:
        """A one-word summary for older screens: OWNER, MANAGER, MEMBER or VIEWER."""
        if self.owner:
            return AccountMemberRole.OWNER.value
        if self.can_manage:
            return AccountMemberRole.MANAGER.value
        return AccountMemberRole.MEMBER.value if self.can_contribute else AccountMemberRole.VIEWER.value

    def can_view(self, module: ProjectModule | str) -> bool:
        return self.allows(area_of(module), "VIEW")

    def can_edit(self, module: ProjectModule | str, action: str = "EDIT") -> bool:
        return self.allows(area_of(module), action)

    @property
    def editable_modules(self) -> list[str]:
        return [module for module in ALL_MODULES if self.can_edit(module)]

    @property
    def visible_modules(self) -> list[str]:
        return [module for module in ALL_MODULES if self.can_view(module)]


OWNER_PERMISSION = Permission(owner=True)


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
        role = db.get(AccountRole, membership.role_id) if membership.role_id else None
        if role is None or role.account_id != account_id or seat_of(role) != membership.role:
            # No role, or one that no longer matches the seat (the seat was changed on its own): the seat decides.
            legacy_key = LEGACY_ROLE_KEYS.get(membership.role, "VIEWER")
            role = db.scalars(
                select(AccountRole).where(AccountRole.account_id == account_id, AccountRole.key == legacy_key)
            ).first()
        self.role = role
        self.is_owner = membership.role == AccountMemberRole.OWNER.value or (role is not None and role.key == "OWNER")
        if role is not None:
            self.rights = rights_of(role.permissions)
            self.scope_all = role.scope == SCOPE_ALL
        else:
            # An account whose roles were never seeded: use the built-in definition.
            default = DEFAULT_ROLES_BY_KEY[LEGACY_ROLE_KEYS.get(membership.role, "VIEWER")]
            self.rights = rights_of(default["permissions"])  # type: ignore[arg-type]
            self.scope_all = default["scope"] == SCOPE_ALL
        billing = db.get(AccountBilling, account_id)
        self.read_only = bool(billing and billing.is_read_only)

        grants = db.scalars(
            select(AccessGrant).where(AccessGrant.account_id == account_id, AccessGrant.user_id == user.id)
        ).all()
        self.portfolio_grants = {g.portfolio_id: g for g in grants if g.portfolio_id is not None}
        self.program_grants = {g.program_id: g for g in grants if g.program_id is not None}
        self.project_grants = {g.project_id: g for g in grants if g.project_id is not None}
        self._allocated_project_ids: set[UUID] | None = None
        self._listed_resource_ids: set[UUID] | None = None
        self._visible_project_ids: set[UUID] | None = None
        self._program_scope_ids: set[UUID] | None = None

    # Rights that don't depend on a project

    def allows(self, area: str, action: str = "VIEW") -> bool:
        return self.is_owner or action in self.rights.get(area, frozenset())

    def require_area(self, area: str, action: str = "VIEW") -> None:
        """Account-wide rights, e.g. editing resources or creating a portfolio."""
        if not self.allows(area, action):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")
        if action != "VIEW":
            self.require_writable()

    def permission(self, level: str) -> Permission:
        return OWNER_PERMISSION if self.is_owner else Permission(rights=self.rights, level=level)

    # Assignments

    def allocated_project_ids(self) -> set[UUID]:
        """Projects the user's resource is assigned or allocated to.

        Assigned on Resources > Capacity (Add Resource, or a weekly-hours override), or allocated on one of the
        project's tasks.
        """
        if self._allocated_project_ids is None:
            resource_ids = select(Resource.id).where(Resource.account_id == self.account_id, Resource.user_id == self.user.id)
            assigned = select(ResourceAssignment.project_id).where(
                ResourceAssignment.resource_id.in_(resource_ids), ResourceAssignment.project_id.is_not(None)
            )
            weekly = select(ResourceProjectAllocation.project_id).where(ResourceProjectAllocation.resource_id.in_(resource_ids))
            on_tasks = (
                select(Task.project_id)
                .join(ResourceAllocation, ResourceAllocation.task_id == Task.id)
                .where(ResourceAllocation.resource_id.in_(resource_ids))
            )
            self._allocated_project_ids = (
                set(self.db.scalars(assigned).all())
                | set(self.db.scalars(weekly).all())
                | set(self.db.scalars(on_tasks).all())
            )
        return self._allocated_project_ids

    def is_assigned(self, portfolio_id: UUID, program_id: UUID, project_id: UUID) -> bool:
        return (
            portfolio_id in self.portfolio_grants
            or program_id in self.program_grants
            or project_id in self.project_grants
            or project_id in self.allocated_project_ids()
        )

    # Permissions on one item

    def for_project(self, project: Project) -> Permission | None:
        return self.project_permission(project.portfolio_id, project.program_id, project.id)

    def project_permission(self, portfolio_id: UUID, program_id: UUID, project_id: UUID) -> Permission | None:
        if self.is_owner:
            return OWNER_PERMISSION
        if self.scope_all or self.is_assigned(portfolio_id, program_id, project_id):
            return self.permission("PROJECT")
        return None

    def for_program(self, program: Program) -> Permission | None:
        """Program-wide permission: assigned to the program or its portfolio, not just to one of its projects."""
        if self.is_owner:
            return OWNER_PERMISSION
        if self.scope_all or program.portfolio_id in self.portfolio_grants or program.id in self.program_grants:
            return self.permission("PROGRAM")
        return None

    def for_portfolio(self, portfolio: Portfolio) -> Permission | None:
        if self.is_owner:
            return OWNER_PERMISSION
        if self.scope_all or portfolio.id in self.portfolio_grants:
            return self.permission("PORTFOLIO")
        return None

    # Checks that raise

    def require(
        self,
        permission: Permission | None,
        *,
        module: ProjectModule | None,
        manage: bool,
        view: ProjectModule | None = None,
        action: str = "EDIT",
    ) -> Permission:
        """module: `action` on that module; view: just seeing it; manage: `action` on the item itself; neither: seeing it."""
        if permission is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)
        if view is not None and not permission.can_view(view):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MODULE_HIDDEN)
        if (manage and not permission.allows(LEVEL_AREAS[permission.level], action)) or (
            module is not None and not permission.can_edit(module, action)
        ):
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
        action: str = "EDIT",
    ) -> Permission:
        """View access by default; `action` on `module`, sight of `view`, or `action` on the project with manage=True."""
        return self.require(self.for_project(project), module=module, manage=manage, view=view, action=action)

    def require_program(
        self,
        program: Program,
        *,
        module: ProjectModule | None = None,
        manage: bool = False,
        view: ProjectModule | None = None,
        action: str = "EDIT",
    ) -> Permission:
        return self.require(self.for_program(program), module=module, manage=manage, view=view, action=action)

    def require_portfolio(self, portfolio: Portfolio, *, manage: bool = False, action: str = "EDIT") -> Permission:
        return self.require(self.for_portfolio(portfolio), module=None, manage=manage, action=action)

    def require_to_create_in(self, parent: Portfolio | Program, area: str) -> Permission:
        """Creating a program in a portfolio, or a project in a program: reach the parent and hold `area` CREATE."""
        permission = self.require_portfolio(parent) if isinstance(parent, Portfolio) else self.require_program(parent)
        if not permission.allows(area, "CREATE"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=INSUFFICIENT_PROJECT_PERMISSION)
        self.require_writable()
        return permission

    def require_item(
        self,
        *,
        project_id: UUID | None,
        program_id: UUID | None,
        module: ProjectModule | None = None,
        manage: bool = False,
        view: ProjectModule | None = None,
        action: str = "EDIT",
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
            return self.require_project(project, module=module, manage=manage, view=view, action=action)
        program = self.db.get(Program, program_id) if program_id is not None else None
        if program is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ACCESS_DENIED)
        return self.require_program(program, module=module, manage=manage, view=view, action=action)

    def require_entity(self, entity_type: str, record: Any, *, contribute: bool = False) -> Permission | None:
        """Access to any record that comments, attachments and activity hang off.

        contribute=False needs view access; True needs edit access to the record's module
        (or, on a portfolio, program or project itself, any right beyond viewing).
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
            if not permission.can_contribute:
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

    # What is visible, for list queries. None means everything in the account (owners, and roles scoped to ALL).
    # A role's rights are the same on every project it reaches, so a module is visible everywhere or nowhere.

    def can_see_module(self, module: ProjectModule | None) -> bool:
        return module is None or self.allows(area_of(module), "VIEW")

    def visible_project_ids(self, module: ProjectModule | None = None) -> set[UUID] | None:
        """Projects the user can see; with a module, only those where that module is visible too."""
        if self.is_owner:
            return None
        if not self.can_see_module(module):
            return set()
        if self.scope_all:
            return None
        if self._visible_project_ids is None:
            assigned = select(Project.id).where(
                Project.account_id == self.account_id,
                or_(
                    Project.portfolio_id.in_(self.portfolio_grants),
                    Project.program_id.in_(self.program_grants),
                    Project.id.in_(self.project_grants),
                    Project.id.in_(self.allocated_project_ids()),
                ),
            )
            self._visible_project_ids = set(self.db.scalars(assigned).all())
        return self._visible_project_ids

    def program_scope_ids(self, module: ProjectModule | None = None) -> set[UUID] | None:
        """Programs the user is assigned to program-wide (directly or through the portfolio).

        With a module, only when that module is visible.
        """
        if self.is_owner:
            return None
        if not self.can_see_module(module):
            return set()
        if self.scope_all:
            return None
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
        """Programs shown in navigation: program-wide assignments, plus the parents of visible projects."""
        if self.is_owner or self.scope_all:
            return None
        project_ids = self.visible_project_ids() or set()
        parents = set(self.db.scalars(select(Project.program_id).where(Project.id.in_(project_ids))).all())
        return (self.program_scope_ids() or set()) | parents

    def visible_portfolio_ids(self) -> set[UUID] | None:
        if self.is_owner or self.scope_all:
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

    def visible_activity(self, entries: list[Any]) -> list[Any]:
        """The activity entries about records in modules this user can see.

        The records are loaded in one query per entity type rather than one per entry.
        """
        if self.is_owner:
            return list(entries)
        ids_by_type: dict[str, set[UUID]] = defaultdict(set)
        for entry in entries:
            ids_by_type[entry.entity_type.upper()].add(entry.entity_id)

        # A decision option is visible when its decision is.
        option_ids = ids_by_type.pop("DECISION_OPTION", set())
        decision_of_option: dict[UUID, UUID] = {}
        if option_ids:
            decision_of_option = dict(
                self.db.execute(select(DecisionOption.id, DecisionOption.decision_id).where(DecisionOption.id.in_(option_ids))).all()
            )
            ids_by_type["DECISION"] |= set(decision_of_option.values())

        records: dict[tuple[str, UUID], Any] = {}
        for entity_type, ids in ids_by_type.items():
            if entity_type not in ENTITY_MODULES:
                continue
            model = COMMENTABLE_MODELS[entity_type]
            for record in self.db.scalars(select(model).where(model.id.in_(ids))):
                records[(entity_type, record.id)] = record

        def can_view(entry: Any) -> bool:
            entity_type, entity_id = entry.entity_type.upper(), entry.entity_id
            if entity_type == "DECISION_OPTION":
                entity_type, entity_id = "DECISION", decision_of_option.get(entity_id, entity_id)
            if entity_type not in ENTITY_MODULES:
                return True
            record = records.get((entity_type, entity_id))
            if record is None:
                return False
            try:
                self.require_entity(entity_type, record)
            except HTTPException:
                return False
            return True

        return [entry for entry in entries if can_view(entry)]

    def listed_resource_ids(self) -> set[UUID] | None:
        """Resources this user may see on the Resources pages (capacity, availability, costs).

        None means every listed resource: owners, and roles scoped to ALL with Resources > View. Without that right,
        none. Otherwise themselves plus the people working in what they can see: assigned to one of their projects,
        programs or portfolios, or with hours or tasks on one of their projects.
        """
        if self.is_owner:
            return None
        if not self.allows("RESOURCES", "VIEW"):
            return set()
        if self.scope_all:
            return None
        if self._listed_resource_ids is None:
            project_ids = self.visible_project_ids() or set()
            task_project_ids = self.visible_project_ids(ProjectModule.TASKS) or set()
            program_ids = self.program_scope_ids() or set()
            portfolio_ids = set(self.portfolio_grants)
            ids: set[UUID] = set(
                self.db.scalars(
                    select(Resource.id).where(Resource.account_id == self.account_id, Resource.user_id == self.user.id)
                ).all()
            )
            ids |= set(
                self.db.scalars(
                    select(ResourceAssignment.resource_id).where(
                        ResourceAssignment.account_id == self.account_id,
                        or_(
                            ResourceAssignment.project_id.in_(project_ids) if project_ids else false(),
                            ResourceAssignment.program_id.in_(program_ids) if program_ids else false(),
                            ResourceAssignment.portfolio_id.in_(portfolio_ids) if portfolio_ids else false(),
                        ),
                    )
                ).all()
            )
            if project_ids:
                ids |= set(
                    self.db.scalars(
                        select(ResourceProjectAllocation.resource_id).where(ResourceProjectAllocation.project_id.in_(project_ids))
                    ).all()
                )
            if task_project_ids:
                ids |= set(
                    self.db.scalars(
                        select(ResourceAllocation.resource_id)
                        .join(Task, Task.id == ResourceAllocation.task_id)
                        .where(Task.project_id.in_(task_project_ids))
                    ).all()
                )
                # Task assignees are recorded by login or by name; match them to resources either way.
                assignees = self.db.execute(
                    select(TaskAssignment.user_id, TaskAssignment.resource_name)
                    .join(Task, Task.id == TaskAssignment.task_id)
                    .where(Task.project_id.in_(task_project_ids))
                ).all()
                user_ids = {user_id for user_id, _ in assignees if user_id is not None}
                names = {" ".join(name.split()).lower() for _, name in assignees if name}
                if user_ids or names:
                    for resource_id, user_id, name in self.db.execute(
                        select(Resource.id, Resource.user_id, Resource.name).where(Resource.account_id == self.account_id)
                    ).all():
                        if user_id in user_ids or " ".join((name or "").split()).lower() in names:
                            ids.add(resource_id)
            self._listed_resource_ids = ids
        return self._listed_resource_ids

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
    if entity_type == "SUPPORT_TICKET":
        return can_see_support_ticket(db, account_id=account_id, user=user, ticket_id=entity_id)
    model = Comment if entity_type == "COMMENT" else COMMENTABLE_MODELS.get(entity_type)
    record = db.get(model, entity_id) if model is not None else None
    if record is None:
        return False
    try:
        AccessScope(db, account_id=account_id, user=user).require_entity(entity_type, record)
    except HTTPException:
        return False
    return True


def can_see_support_ticket(db: Session, *, account_id: UUID, user: User, ticket_id: UUID) -> bool:
    """A support ticket is seen by the person who raised it and the account's owners (and Project Hub support)."""
    from app.models.support_ticket import SupportTicket

    ticket = db.get(SupportTicket, ticket_id)
    if ticket is None or ticket.account_id != account_id:
        return False
    if ticket.requester_user_id == user.id:
        return True
    membership = AccountMemberRepository(db).get_for_user(account_id=account_id, user_id=user.id)
    return membership is not None and membership.role == AccountMemberRole.OWNER.value
