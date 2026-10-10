"""Project-scoped logs that share the RAID machinery: dependencies, change requests,
requirements and test cases."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params
from app.models.user import User
from app.schemas.project_logs import (
    ChangeRequestCreate,
    ChangeRequestRead,
    ChangeRequestUpdate,
    DependencyCreate,
    DependencyRead,
    DependencyUpdate,
    RequirementCreate,
    RequirementRead,
    RequirementUpdate,
    TestCaseCreate,
    TestCaseRead,
    TestCaseUpdate,
)
from app.services.auth import get_current_user
from app.services.raid import RaidService


router = APIRouter(prefix="/api/v1", tags=["project-logs"])


def register_project_log_routes(
    *,
    kind: str,
    collection: str,
    label: str,
    read_model: type[BaseModel],
    create_model: type[BaseModel],
    update_model: type[BaseModel],
) -> None:
    def list_for_project(
        project_id: UUID,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        sort: str | None = Query(default=None),
        pagination: PaginationParams = Depends(get_pagination_params),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> list[BaseModel] | dict[str, object]:
        return RaidService(db).list_items(
            kind=kind,
            project_id=project_id,
            current_user=current_user,
            status_id=status_id,
            priority_id=priority_id,
            sort=sort,
            pagination=pagination,
        )

    def list_for_account(
        account_id: UUID,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        search: str | None = None,
        sort: str | None = Query(default=None, description="Safe sort field, prefix with '-' for descending."),
        pagination: PaginationParams = Depends(get_pagination_params),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> list[BaseModel] | dict[str, object]:
        return RaidService(db).list_account_items(
            kind=kind,
            account_id=account_id,
            current_user=current_user,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            priority_id=priority_id,
            search=search,
            sort=sort,
            pagination=pagination,
        )

    def create(
        project_id: UUID,
        item_in: create_model,  # type: ignore[valid-type]
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> dict[str, object]:
        return RaidService(db).create_item(
            kind=kind,
            project_id=project_id,
            item_in=item_in,
            current_user=current_user,
        )

    def get(
        item_id: UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> dict[str, object]:
        return RaidService(db).get_item(kind=kind, item_id=item_id, current_user=current_user)

    def update(
        item_id: UUID,
        item_in: update_model,  # type: ignore[valid-type]
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> dict[str, object]:
        return RaidService(db).update_item(kind=kind, item_id=item_id, item_in=item_in, current_user=current_user)

    def delete(
        item_id: UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> Response:
        RaidService(db).delete_item(kind=kind, item_id=item_id, current_user=current_user)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    router.add_api_route(
        f"/projects/{{project_id}}/{collection}",
        list_for_project,
        methods=["GET"],
        response_model=list[read_model] | PaginatedResponse[read_model],  # type: ignore[valid-type]
        operation_id=f"list_project_{kind}s",
        summary=f"List project {label}s",
    )
    router.add_api_route(
        f"/accounts/{{account_id}}/{collection}",
        list_for_account,
        methods=["GET"],
        response_model=list[read_model] | PaginatedResponse[read_model],  # type: ignore[valid-type]
        operation_id=f"list_account_{kind}s",
        summary=f"List account {label}s",
    )
    router.add_api_route(
        f"/projects/{{project_id}}/{collection}",
        create,
        methods=["POST"],
        response_model=read_model,
        status_code=status.HTTP_201_CREATED,
        operation_id=f"create_{kind}",
        summary=f"Create {label}",
    )
    router.add_api_route(
        f"/{collection}/{{item_id}}",
        get,
        methods=["GET"],
        response_model=read_model,
        operation_id=f"get_{kind}",
        summary=f"Get {label}",
    )
    router.add_api_route(
        f"/{collection}/{{item_id}}",
        update,
        methods=["PATCH"],
        response_model=read_model,
        operation_id=f"update_{kind}",
        summary=f"Update {label}",
    )
    router.add_api_route(
        f"/{collection}/{{item_id}}",
        delete,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        operation_id=f"delete_{kind}",
        summary=f"Delete {label}",
    )


register_project_log_routes(
    kind="dependency",
    collection="dependencies",
    label="dependency",
    read_model=DependencyRead,
    create_model=DependencyCreate,
    update_model=DependencyUpdate,
)
register_project_log_routes(
    kind="change_request",
    collection="change-requests",
    label="change request",
    read_model=ChangeRequestRead,
    create_model=ChangeRequestCreate,
    update_model=ChangeRequestUpdate,
)
register_project_log_routes(
    kind="requirement",
    collection="requirements",
    label="requirement",
    read_model=RequirementRead,
    create_model=RequirementCreate,
    update_model=RequirementUpdate,
)
register_project_log_routes(
    kind="test_case",
    collection="test-cases",
    label="test case",
    read_model=TestCaseRead,
    create_model=TestCaseCreate,
    update_model=TestCaseUpdate,
)
