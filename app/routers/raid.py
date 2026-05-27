from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params
from app.models.user import User
from app.schemas.raid import (
    AccountAssumptionRead,
    AccountDecisionRead,
    AccountIssueRead,
    AccountRiskRead,
    AssumptionCreate,
    AssumptionRead,
    AssumptionUpdate,
    DecisionCreate,
    DecisionOptionCreate,
    DecisionOptionRead,
    DecisionOptionUpdate,
    DecisionRead,
    DecisionUpdate,
    IssueCreate,
    IssueRead,
    IssueUpdate,
    RiskCreate,
    RiskRead,
    RiskUpdate,
)
from app.services.auth import get_current_user
from app.services.raid import RaidService


router = APIRouter(prefix="/api/v1", tags=["raid"])


@router.get("/projects/{project_id}/risks", response_model=list[RiskRead] | PaginatedResponse[RiskRead])
def list_risks(
    project_id: UUID,
    status_id: UUID | None = None,
    priority_id: UUID | None = None,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[RiskRead] | dict[str, object]:
    return RaidService(db).list_risks(
        project_id=project_id,
        current_user=current_user,
        status_id=status_id,
        priority_id=priority_id,
        sort=sort,
        pagination=pagination,
    )


@router.get(
    "/accounts/{account_id}/risks",
    response_model=list[AccountRiskRead] | PaginatedResponse[AccountRiskRead],
    summary="List account risks",
    description=(
        "List risks across all projects in an account. Supports optional project/program filters, "
        "status/priority/assignee filters, text search, safe sorting, and optional pagination."
    ),
)
def list_account_risks(
    account_id: UUID,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    status_id: UUID | None = None,
    priority_id: UUID | None = None,
    assigned_to: UUID | None = None,
    search: str | None = None,
    sort: str | None = Query(default=None, description="Safe sort field, prefix with '-' for descending."),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountRiskRead] | dict[str, object]:
    return RaidService(db).list_account_risks(
        account_id=account_id,
        current_user=current_user,
        project_id=project_id,
        program_id=program_id,
        status_id=status_id,
        priority_id=priority_id,
        assigned_to=assigned_to,
        search=search,
        sort=sort,
        pagination=pagination,
    )


@router.post("/projects/{project_id}/risks", response_model=RiskRead, status_code=status.HTTP_201_CREATED)
def create_risk(
    project_id: UUID,
    risk_in: RiskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RiskRead:
    return RaidService(db).create_risk(project_id=project_id, risk_in=risk_in, current_user=current_user)


@router.get("/risks/{risk_id}", response_model=RiskRead)
def get_risk(
    risk_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RiskRead:
    return RaidService(db).get_risk(risk_id=risk_id, current_user=current_user)


@router.patch("/risks/{risk_id}", response_model=RiskRead)
def update_risk(
    risk_id: UUID,
    risk_in: RiskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RiskRead:
    return RaidService(db).update_risk(risk_id=risk_id, risk_in=risk_in, current_user=current_user)


@router.delete("/risks/{risk_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_risk(risk_id: UUID) -> dict[str, str]:
    return {"detail": "Risk deletion is not implemented in Phase 4A."}


@router.get("/projects/{project_id}/issues", response_model=list[IssueRead] | PaginatedResponse[IssueRead])
def list_issues(
    project_id: UUID,
    status_id: UUID | None = None,
    priority_id: UUID | None = None,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[IssueRead] | dict[str, object]:
    return RaidService(db).list_issues(
        project_id=project_id,
        current_user=current_user,
        status_id=status_id,
        priority_id=priority_id,
        sort=sort,
        pagination=pagination,
    )


@router.get(
    "/accounts/{account_id}/issues",
    response_model=list[AccountIssueRead] | PaginatedResponse[AccountIssueRead],
    summary="List account issues",
    description=(
        "List issues across all projects in an account. Supports optional project/program filters, "
        "status/priority/assignee filters, text search, safe sorting, and optional pagination."
    ),
)
def list_account_issues(
    account_id: UUID,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    status_id: UUID | None = None,
    priority_id: UUID | None = None,
    assigned_to: UUID | None = None,
    search: str | None = None,
    sort: str | None = Query(default=None, description="Safe sort field, prefix with '-' for descending."),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountIssueRead] | dict[str, object]:
    return RaidService(db).list_account_issues(
        account_id=account_id,
        current_user=current_user,
        project_id=project_id,
        program_id=program_id,
        status_id=status_id,
        priority_id=priority_id,
        assigned_to=assigned_to,
        search=search,
        sort=sort,
        pagination=pagination,
    )


@router.post("/projects/{project_id}/issues", response_model=IssueRead, status_code=status.HTTP_201_CREATED)
def create_issue(
    project_id: UUID,
    issue_in: IssueCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IssueRead:
    return RaidService(db).create_issue(project_id=project_id, issue_in=issue_in, current_user=current_user)


@router.get("/issues/{issue_id}", response_model=IssueRead)
def get_issue(
    issue_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IssueRead:
    return RaidService(db).get_issue(issue_id=issue_id, current_user=current_user)


@router.patch("/issues/{issue_id}", response_model=IssueRead)
def update_issue(
    issue_id: UUID,
    issue_in: IssueUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IssueRead:
    return RaidService(db).update_issue(issue_id=issue_id, issue_in=issue_in, current_user=current_user)


@router.delete("/issues/{issue_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_issue(issue_id: UUID) -> dict[str, str]:
    return {"detail": "Issue deletion is not implemented in Phase 4A."}


@router.get("/projects/{project_id}/assumptions", response_model=list[AssumptionRead] | PaginatedResponse[AssumptionRead])
def list_assumptions(
    project_id: UUID,
    status_id: UUID | None = None,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AssumptionRead] | dict[str, object]:
    return RaidService(db).list_assumptions(
        project_id=project_id,
        current_user=current_user,
        status_id=status_id,
        sort=sort,
        pagination=pagination,
    )


@router.get(
    "/accounts/{account_id}/assumptions",
    response_model=list[AccountAssumptionRead] | PaginatedResponse[AccountAssumptionRead],
    summary="List account assumptions",
    description=(
        "List assumptions across all projects in an account. Supports optional project/program filters, "
        "status filter, text search, safe sorting, and optional pagination."
    ),
)
def list_account_assumptions(
    account_id: UUID,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    status_id: UUID | None = None,
    search: str | None = None,
    sort: str | None = Query(default=None, description="Safe sort field, prefix with '-' for descending."),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountAssumptionRead] | dict[str, object]:
    return RaidService(db).list_account_assumptions(
        account_id=account_id,
        current_user=current_user,
        project_id=project_id,
        program_id=program_id,
        status_id=status_id,
        search=search,
        sort=sort,
        pagination=pagination,
    )


@router.post(
    "/projects/{project_id}/assumptions",
    response_model=AssumptionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_assumption(
    project_id: UUID,
    assumption_in: AssumptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssumptionRead:
    return RaidService(db).create_assumption(
        project_id=project_id,
        assumption_in=assumption_in,
        current_user=current_user,
    )


@router.get("/assumptions/{assumption_id}", response_model=AssumptionRead)
def get_assumption(
    assumption_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssumptionRead:
    return RaidService(db).get_assumption(assumption_id=assumption_id, current_user=current_user)


@router.patch("/assumptions/{assumption_id}", response_model=AssumptionRead)
def update_assumption(
    assumption_id: UUID,
    assumption_in: AssumptionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AssumptionRead:
    return RaidService(db).update_assumption(
        assumption_id=assumption_id,
        assumption_in=assumption_in,
        current_user=current_user,
    )


@router.delete("/assumptions/{assumption_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_assumption(assumption_id: UUID) -> dict[str, str]:
    return {"detail": "Assumption deletion is not implemented in Phase 4A."}


@router.get("/projects/{project_id}/decisions", response_model=list[DecisionRead] | PaginatedResponse[DecisionRead])
def list_decisions(
    project_id: UUID,
    status_id: UUID | None = None,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DecisionRead] | dict[str, object]:
    return RaidService(db).list_decisions(
        project_id=project_id,
        current_user=current_user,
        status_id=status_id,
        sort=sort,
        pagination=pagination,
    )


@router.get(
    "/accounts/{account_id}/decisions",
    response_model=list[AccountDecisionRead] | PaginatedResponse[AccountDecisionRead],
    summary="List account decisions",
    description=(
        "List decisions across all projects in an account. Supports optional project/program filters, "
        "status filter, text search, safe sorting, and optional pagination."
    ),
)
def list_account_decisions(
    account_id: UUID,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    status_id: UUID | None = None,
    search: str | None = None,
    sort: str | None = Query(default=None, description="Safe sort field, prefix with '-' for descending."),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountDecisionRead] | dict[str, object]:
    return RaidService(db).list_account_decisions(
        account_id=account_id,
        current_user=current_user,
        project_id=project_id,
        program_id=program_id,
        status_id=status_id,
        search=search,
        sort=sort,
        pagination=pagination,
    )


@router.post("/projects/{project_id}/decisions", response_model=DecisionRead, status_code=status.HTTP_201_CREATED)
def create_decision(
    project_id: UUID,
    decision_in: DecisionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionRead:
    return RaidService(db).create_decision(
        project_id=project_id,
        decision_in=decision_in,
        current_user=current_user,
    )


@router.get("/decisions/{decision_id}", response_model=DecisionRead)
def get_decision(
    decision_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionRead:
    return RaidService(db).get_decision(decision_id=decision_id, current_user=current_user)


@router.patch("/decisions/{decision_id}", response_model=DecisionRead)
def update_decision(
    decision_id: UUID,
    decision_in: DecisionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionRead:
    return RaidService(db).update_decision(
        decision_id=decision_id,
        decision_in=decision_in,
        current_user=current_user,
    )


@router.delete("/decisions/{decision_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_decision(decision_id: UUID) -> dict[str, str]:
    return {"detail": "Decision deletion is not implemented in Phase 4A."}


@router.get("/decisions/{decision_id}/options", response_model=list[DecisionOptionRead])
def list_decision_options(
    decision_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DecisionOptionRead]:
    return RaidService(db).list_decision_options(decision_id=decision_id, current_user=current_user)


@router.post(
    "/decisions/{decision_id}/options",
    response_model=DecisionOptionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_decision_option(
    decision_id: UUID,
    option_in: DecisionOptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionOptionRead:
    return RaidService(db).create_decision_option(
        decision_id=decision_id,
        option_in=option_in,
        current_user=current_user,
    )


@router.get("/decision-options/{option_id}", response_model=DecisionOptionRead)
def get_decision_option(
    option_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionOptionRead:
    return RaidService(db).get_decision_option(option_id=option_id, current_user=current_user)


@router.patch("/decision-options/{option_id}", response_model=DecisionOptionRead)
def update_decision_option(
    option_id: UUID,
    option_in: DecisionOptionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionOptionRead:
    return RaidService(db).update_decision_option(
        option_id=option_id,
        option_in=option_in,
        current_user=current_user,
    )


@router.delete("/decision-options/{option_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_decision_option(
    option_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    RaidService(db).delete_decision_option(option_id=option_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)