from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.resources import (
    AccountCapacityForecastRead,
    ResourceAllocationCreate,
    ResourceAllocationRead,
    ResourceAllocationUpdate,
    ResourceAnalysisRead,
    ResourceCalendarRead,
    ResourceCapacityForecastRead,
    ResourceCreate,
    ResourceRecommendationRead,
    ResourceRead,
    ResourceTimeOffCreate,
    ResourceTimeOffRead,
    ResourceTimeOffUpdate,
    ResourceUpdate,
)
from app.services.auth import get_current_user
from app.services.resources import ResourceService


router = APIRouter(prefix="/api/v1", tags=["resources"])


@router.get("/accounts/{account_id}/resources", response_model=list[ResourceRead])
def list_resources(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResourceRead]:
    return ResourceService(db).list_resources(account_id=account_id, current_user=current_user)


@router.post("/accounts/{account_id}/resources", response_model=ResourceRead, status_code=status.HTTP_201_CREATED)
def create_resource(
    account_id: UUID,
    resource_in: ResourceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceRead:
    return ResourceService(db).create_resource(account_id=account_id, resource_in=resource_in, current_user=current_user)


@router.get("/resources/{resource_id}", response_model=ResourceRead)
def get_resource(
    resource_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceRead:
    return ResourceService(db).get_resource(resource_id=resource_id, current_user=current_user)


@router.patch("/resources/{resource_id}", response_model=ResourceRead)
def update_resource(
    resource_id: UUID,
    resource_in: ResourceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceRead:
    return ResourceService(db).update_resource(resource_id=resource_id, resource_in=resource_in, current_user=current_user)


@router.delete("/resources/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource(
    resource_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    ResourceService(db).delete_resource(resource_id=resource_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/resources/{resource_id}/allocations", response_model=list[ResourceAllocationRead])
def list_resource_allocations(
    resource_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResourceAllocationRead]:
    return ResourceService(db).list_resource_allocations(resource_id=resource_id, current_user=current_user)


@router.get("/resources/{resource_id}/time-off", response_model=list[ResourceTimeOffRead])
def list_resource_time_off(
    resource_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResourceTimeOffRead]:
    return ResourceService(db).list_resource_time_off(resource_id=resource_id, current_user=current_user)


@router.post("/resources/{resource_id}/time-off", response_model=ResourceTimeOffRead, status_code=status.HTTP_201_CREATED)
def create_resource_time_off(
    resource_id: UUID,
    time_off_in: ResourceTimeOffCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceTimeOffRead:
    return ResourceService(db).create_time_off(
        resource_id=resource_id,
        time_off_in=time_off_in,
        current_user=current_user,
    )


@router.get("/resources/{resource_id}/capacity-forecast", response_model=ResourceCapacityForecastRead)
def get_resource_capacity_forecast(
    resource_id: UUID,
    start_date: date,
    end_date: date,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return ResourceService(db).get_resource_capacity_forecast(
        resource_id=resource_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        project_id=project_id,
        program_id=program_id,
    )


@router.get("/accounts/{account_id}/resource-calendar", response_model=ResourceCalendarRead)
def get_resource_calendar(
    account_id: UUID,
    start_date: date,
    end_date: date,
    resource_id: UUID | None = None,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return ResourceService(db).get_resource_calendar(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        resource_id=resource_id,
        project_id=project_id,
        program_id=program_id,
    )


@router.get("/accounts/{account_id}/capacity-forecast", response_model=AccountCapacityForecastRead)
def get_account_capacity_forecast(
    account_id: UUID,
    start_date: date,
    end_date: date,
    resource_id: UUID | None = None,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return ResourceService(db).get_account_capacity_forecast(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        resource_id=resource_id,
        project_id=project_id,
        program_id=program_id,
    )


@router.get("/accounts/{account_id}/resource-analysis", response_model=ResourceAnalysisRead)
def get_resource_analysis(
    account_id: UUID,
    start_date: date,
    end_date: date,
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    resource_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return ResourceService(db).get_resource_analysis(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        project_id=project_id,
        program_id=program_id,
        resource_id=resource_id,
    )


@router.get("/tasks/{task_id}/resource-recommendations", response_model=list[ResourceRecommendationRead])
def get_task_resource_recommendations(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    return ResourceService(db).get_task_resource_recommendations(task_id=task_id, current_user=current_user)


@router.post(
    "/tasks/{task_id}/resource-allocations",
    response_model=ResourceAllocationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_resource_allocation(
    task_id: UUID,
    allocation_in: ResourceAllocationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceAllocationRead:
    return ResourceService(db).create_allocation(
        task_id=task_id,
        allocation_in=allocation_in,
        current_user=current_user,
    )


@router.patch("/resource-allocations/{allocation_id}", response_model=ResourceAllocationRead)
def update_resource_allocation(
    allocation_id: UUID,
    allocation_in: ResourceAllocationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceAllocationRead:
    return ResourceService(db).update_allocation(
        allocation_id=allocation_id,
        allocation_in=allocation_in,
        current_user=current_user,
    )


@router.delete("/resource-allocations/{allocation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource_allocation(
    allocation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    ResourceService(db).delete_allocation(allocation_id=allocation_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/resource-time-off/{time_off_id}", response_model=ResourceTimeOffRead)
def update_resource_time_off(
    time_off_id: UUID,
    time_off_in: ResourceTimeOffUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResourceTimeOffRead:
    return ResourceService(db).update_time_off(
        time_off_id=time_off_id,
        time_off_in=time_off_in,
        current_user=current_user,
    )


@router.delete("/resource-time-off/{time_off_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource_time_off(
    time_off_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    ResourceService(db).delete_time_off(time_off_id=time_off_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)