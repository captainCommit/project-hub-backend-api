"""Managing who has which role on which portfolio, program or project."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.access_grant import AccessGrant, GrantRole, ProjectModule
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.billing import AccountBilling
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.user import User
from app.schemas.access import (
    AccessGrantCreate,
    AccessGrantRead,
    AccessGrantUpdate,
    AccessGrantUser,
    GrantScope,
    MyAccessRead,
    PermissionRead,
)
from app.schemas.billing import BillingState
from app.services.access import ROLE_RANK, AccessScope, Permission, access_for, forget_access
from app.services.billing import BillingService


OWNERS_NEED_NO_GRANTS = "Owners already have access to everything in the account."


class AccessGrantService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # Reading

    def list_grants(
        self,
        *,
        account_id: UUID,
        current_user: User,
        portfolio_id: UUID | None = None,
        program_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> list[AccessGrantRead]:
        """Grants covering one item (its own and those inherited from parents), or every grant for owners."""
        scope = access_for(self.db, account_id=account_id, user=current_user)
        statement = select(AccessGrant).where(AccessGrant.account_id == account_id)
        if project_id is not None:
            project = self.get_in_account(Project, project_id, account_id)
            scope.require_project(project)
            statement = statement.where(
                or_(
                    AccessGrant.project_id == project.id,
                    AccessGrant.program_id == project.program_id,
                    AccessGrant.portfolio_id == project.portfolio_id,
                )
            )
            own_column = "project_id"
        elif program_id is not None:
            program = self.get_in_account(Program, program_id, account_id)
            scope.require_visible_program(program)
            statement = statement.where(
                or_(AccessGrant.program_id == program.id, AccessGrant.portfolio_id == program.portfolio_id)
            )
            own_column = "program_id"
        elif portfolio_id is not None:
            portfolio = self.get_in_account(Portfolio, portfolio_id, account_id)
            scope.require_visible_portfolio(portfolio)
            statement = statement.where(AccessGrant.portfolio_id == portfolio.id)
            own_column = "portfolio_id"
        else:
            if not scope.is_owner:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")
            own_column = None
        grants = list(self.db.scalars(statement.order_by(AccessGrant.created_at)).all())
        return self.to_reads(grants, own_column=own_column)

    def my_access(self, *, account_id: UUID, current_user: User) -> MyAccessRead:
        scope = access_for(self.db, account_id=account_id, user=current_user)

        def read(permission: Permission | None) -> PermissionRead | None:
            if permission is None:
                return None
            return PermissionRead(
                role=permission.role,
                can_manage=permission.can_manage,
                editable_modules=[ProjectModule(module) for module in permission.editable_modules],
                visible_modules=[ProjectModule(module) for module in permission.visible_modules],
            )

        def visible(model: type, ids: set[UUID] | None) -> list:
            statement = select(model).where(model.account_id == account_id)
            if ids is not None:
                statement = statement.where(model.id.in_(ids))
            return list(self.db.scalars(statement).all())

        return MyAccessRead(
            account_role=scope.account_role,
            is_owner=scope.is_owner,
            billing=BillingState(**BillingService.state(self.db.get(AccountBilling, account_id))),
            portfolios={p.id: read(scope.for_portfolio(p)) for p in visible(Portfolio, scope.visible_portfolio_ids())},
            programs={p.id: read(scope.for_program(p)) for p in visible(Program, scope.visible_program_ids())},
            projects={p.id: read(scope.for_project(p)) for p in visible(Project, scope.visible_project_ids())},
        )

    # Changing

    def create_grant(self, *, account_id: UUID, grant_in: AccessGrantCreate, current_user: User) -> AccessGrantRead:
        scope = access_for(self.db, account_id=account_id, user=current_user)
        self.require_manage(scope, grant_in)
        grant = self.apply_grant(
            account_id=account_id,
            user_id=grant_in.user_id,
            role=grant_in.role,
            target=grant_in,
            modules=grant_in.modules,
            view_modules=grant_in.view_modules,
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(grant)
        forget_access(self.db)
        return self.to_reads([grant], own_column=self.own_column(grant))[0]

    def update_grant(self, *, grant_id: UUID, grant_in: AccessGrantUpdate, current_user: User) -> AccessGrantRead:
        grant = self.get_grant_or_404(grant_id)
        scope = access_for(self.db, account_id=grant.account_id, user=current_user)
        self.require_manage(scope, self.scope_of(grant))
        role = grant_in.role or GrantRole(grant.role)
        self.require_seat_allows(account_id=grant.account_id, user_id=grant.user_id, role=role)
        modules = grant_in.modules if grant_in.modules is not None else [ProjectModule(m) for m in grant.modules or []]
        if "view_modules" in grant_in.model_fields_set:
            view_modules = grant_in.view_modules
        else:
            view_modules = None if grant.view_modules is None else [ProjectModule(m) for m in grant.view_modules]
        self.set_role_and_modules(grant, role=role, modules=modules, view_modules=view_modules)
        self.db.commit()
        self.db.refresh(grant)
        forget_access(self.db)
        return self.to_reads([grant], own_column=self.own_column(grant))[0]

    def delete_grant(self, *, grant_id: UUID, current_user: User) -> None:
        grant = self.get_grant_or_404(grant_id)
        scope = access_for(self.db, account_id=grant.account_id, user=current_user)
        self.require_manage(scope, self.scope_of(grant))
        self.db.delete(grant)
        self.db.commit()
        forget_access(self.db)

    def apply_grant(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        role: GrantRole,
        target: GrantScope,
        modules: list[ProjectModule],
        created_by: UUID | None,
        view_modules: list[ProjectModule] | None = None,
    ) -> AccessGrant:
        """Give or change a role on one item, without checking who is asking. The caller commits."""
        self.resolve_target(account_id, target)
        self.require_seat_allows(account_id=account_id, user_id=user_id, role=role)
        grant = self.db.scalar(
            select(AccessGrant).where(
                AccessGrant.user_id == user_id,
                AccessGrant.portfolio_id == target.portfolio_id if target.portfolio_id else AccessGrant.portfolio_id.is_(None),
                AccessGrant.program_id == target.program_id if target.program_id else AccessGrant.program_id.is_(None),
                AccessGrant.project_id == target.project_id if target.project_id else AccessGrant.project_id.is_(None),
            )
        )
        if grant is None:
            grant = AccessGrant(
                account_id=account_id,
                user_id=user_id,
                portfolio_id=target.portfolio_id,
                program_id=target.program_id,
                project_id=target.project_id,
                created_by=created_by,
            )
            self.db.add(grant)
        self.set_role_and_modules(grant, role=role, modules=modules, view_modules=view_modules)
        self.db.flush()
        return grant

    @staticmethod
    def set_role_and_modules(
        grant: AccessGrant,
        *,
        role: GrantRole,
        modules: list[ProjectModule],
        view_modules: list[ProjectModule] | None,
    ) -> None:
        """Managers see and edit everything; members edit some modules; anything editable is also visible."""
        grant.role = role.value
        grant.modules = [module.value for module in modules] if role == GrantRole.MEMBER else None
        if role == GrantRole.MANAGER or view_modules is None:
            grant.view_modules = None
        else:
            visible = {module.value for module in view_modules} | set(grant.modules or [])
            grant.view_modules = [module.value for module in ProjectModule if module.value in visible]

    # Rules

    def require_manage(self, scope: AccessScope, target: GrantScope) -> None:
        """Owners grant anywhere; managers within what they manage."""
        record = self.resolve_target(scope.account_id, target)
        if isinstance(record, Portfolio):
            scope.require_portfolio(record, manage=True)
        elif isinstance(record, Program):
            scope.require_program(record, manage=True)
        else:
            scope.require_project(record, manage=True)

    def require_seat_allows(self, *, account_id: UUID, user_id: UUID, role: GrantRole) -> None:
        """A grant never goes above the person's account role, so a viewer seat stays read-only."""
        membership = self.db.scalar(
            select(AccountMember).where(AccountMember.account_id == account_id, AccountMember.user_id == user_id)
        )
        if membership is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not a member of this account.")
        if membership.role == AccountMemberRole.OWNER.value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=OWNERS_NEED_NO_GRANTS)
        if ROLE_RANK[role.value] > ROLE_RANK[membership.role]:
            seat = membership.role.lower()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A {seat} seat can't be given the {role.value.lower()} role. Change their account role first.",
            )

    # Helpers

    def resolve_target(self, account_id: UUID, target: GrantScope) -> Portfolio | Program | Project:
        if target.portfolio_id is not None:
            return self.get_in_account(Portfolio, target.portfolio_id, account_id)
        if target.program_id is not None:
            return self.get_in_account(Program, target.program_id, account_id)
        return self.get_in_account(Project, target.project_id, account_id)

    def get_in_account(self, model: type, item_id: UUID | None, account_id: UUID):  # type: ignore[no-untyped-def]
        record = self.db.get(model, item_id) if item_id is not None else None
        if record is None or record.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{model.__name__} not found.")
        return record

    def get_grant_or_404(self, grant_id: UUID) -> AccessGrant:
        grant = self.db.get(AccessGrant, grant_id)
        if grant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access grant not found.")
        return grant

    def scope_of(self, grant: AccessGrant) -> GrantScope:
        return GrantScope(portfolio_id=grant.portfolio_id, program_id=grant.program_id, project_id=grant.project_id)

    def own_column(self, grant: AccessGrant) -> str:
        return "portfolio_id" if grant.portfolio_id else "program_id" if grant.program_id else "project_id"

    def to_reads(self, grants: list[AccessGrant], *, own_column: str | None) -> list[AccessGrantRead]:
        users = {u.id: u for u in self.db.scalars(select(User).where(User.id.in_({g.user_id for g in grants})))}
        roles = {
            (m.account_id, m.user_id): m.role
            for m in self.db.scalars(
                select(AccountMember).where(AccountMember.user_id.in_({g.user_id for g in grants}))
            )
        }
        names: dict[UUID, str] = {}
        for model, ids in (
            (Portfolio, {g.portfolio_id for g in grants if g.portfolio_id}),
            (Program, {g.program_id for g in grants if g.program_id}),
            (Project, {g.project_id for g in grants if g.project_id}),
        ):
            if ids:
                names.update({r.id: r.name for r in self.db.scalars(select(model).where(model.id.in_(ids)))})

        reads = []
        for grant in grants:
            scope_type = "PORTFOLIO" if grant.portfolio_id else "PROGRAM" if grant.program_id else "PROJECT"
            scope_id = grant.portfolio_id or grant.program_id or grant.project_id
            user = users[grant.user_id]
            reads.append(
                AccessGrantRead(
                    id=grant.id,
                    user=AccessGrantUser(
                        id=user.id,
                        email=user.email,
                        full_name=user.full_name,
                        account_role=roles.get((grant.account_id, user.id), ""),
                    ),
                    scope_type=scope_type,
                    scope_id=scope_id,
                    scope_name=names.get(scope_id, ""),
                    role=GrantRole(grant.role),
                    modules=[ProjectModule(m) for m in grant.modules or []],
                    view_modules=None if grant.view_modules is None else [ProjectModule(m) for m in grant.view_modules],
                    inherited=own_column is not None and getattr(grant, own_column) is None,
                    created_at=grant.created_at,
                )
            )
        return reads
