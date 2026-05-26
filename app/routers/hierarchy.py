from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.hierarchy import (
    AccountSidebarRead,
    PortfolioCreate,
    PortfolioOverviewRead,
    PortfolioRead,
    PortfolioUpdate,
    ProgramCreate,
    ProgramOverviewRead,
    ProgramRead,
    ProgramUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.auth import get_current_user
from app.services.hierarchy import HierarchyService


router = APIRouter(prefix="/api/v1", tags=["hierarchy"])


@router.get("/accounts/{account_id}/portfolios", response_model=list[PortfolioRead])
def list_portfolios(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PortfolioRead]:
    return HierarchyService(db).list_portfolios(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/portfolios",
    response_model=PortfolioRead,
    status_code=status.HTTP_201_CREATED,
)
def create_portfolio(
    account_id: UUID,
    portfolio_in: PortfolioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioRead:
    return HierarchyService(db).create_portfolio(
        account_id=account_id,
        portfolio_in=portfolio_in,
        current_user=current_user,
    )


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioRead)
def get_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioRead:
    return HierarchyService(db).get_portfolio(portfolio_id=portfolio_id, current_user=current_user)


@router.patch("/portfolios/{portfolio_id}", response_model=PortfolioRead)
def update_portfolio(
    portfolio_id: UUID,
    portfolio_in: PortfolioUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioRead:
    return HierarchyService(db).update_portfolio(
        portfolio_id=portfolio_id,
        portfolio_in=portfolio_in,
        current_user=current_user,
    )


@router.delete("/portfolios/{portfolio_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_portfolio(portfolio_id: UUID) -> dict[str, str]:
    return {"detail": "Portfolio deletion is not implemented in Phase 3A."}


@router.get("/portfolios/{portfolio_id}/programs", response_model=list[ProgramRead])
def list_programs(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProgramRead]:
    return HierarchyService(db).list_programs(portfolio_id=portfolio_id, current_user=current_user)


@router.post(
    "/portfolios/{portfolio_id}/programs",
    response_model=ProgramRead,
    status_code=status.HTTP_201_CREATED,
)
def create_program(
    portfolio_id: UUID,
    program_in: ProgramCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProgramRead:
    return HierarchyService(db).create_program(
        portfolio_id=portfolio_id,
        program_in=program_in,
        current_user=current_user,
    )


@router.get("/programs/{program_id}", response_model=ProgramRead)
def get_program(
    program_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProgramRead:
    return HierarchyService(db).get_program(program_id=program_id, current_user=current_user)


@router.patch("/programs/{program_id}", response_model=ProgramRead)
def update_program(
    program_id: UUID,
    program_in: ProgramUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProgramRead:
    return HierarchyService(db).update_program(
        program_id=program_id,
        program_in=program_in,
        current_user=current_user,
    )


@router.delete("/programs/{program_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_program(program_id: UUID) -> dict[str, str]:
    return {"detail": "Program deletion is not implemented in Phase 3A."}


@router.get("/programs/{program_id}/projects", response_model=list[ProjectRead])
def list_projects(
    program_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProjectRead]:
    return HierarchyService(db).list_projects(program_id=program_id, current_user=current_user)


@router.post(
    "/programs/{program_id}/projects",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    program_id: UUID,
    project_in: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectRead:
    return HierarchyService(db).create_project(
        program_id=program_id,
        project_in=project_in,
        current_user=current_user,
    )


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectRead:
    return HierarchyService(db).get_project(project_id=project_id, current_user=current_user)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    project_in: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectRead:
    return HierarchyService(db).update_project(
        project_id=project_id,
        project_in=project_in,
        current_user=current_user,
    )


@router.delete("/projects/{project_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_project(project_id: UUID) -> dict[str, str]:
    return {"detail": "Project deletion is not implemented in Phase 3A."}


@router.get("/accounts/{account_id}/sidebar", response_model=AccountSidebarRead)
def get_sidebar(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountSidebarRead:
    return HierarchyService(db).get_sidebar(account_id=account_id, current_user=current_user)


@router.get("/portfolios/{portfolio_id}/overview", response_model=PortfolioOverviewRead)
def get_portfolio_overview(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioOverviewRead:
    return HierarchyService(db).get_portfolio_overview(
        portfolio_id=portfolio_id,
        current_user=current_user,
    )


@router.get("/programs/{program_id}/overview", response_model=ProgramOverviewRead)
def get_program_overview(
    program_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProgramOverviewRead:
    return HierarchyService(db).get_program_overview(
        program_id=program_id,
        current_user=current_user,
    )