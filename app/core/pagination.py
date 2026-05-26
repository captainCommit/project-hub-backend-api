from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Generic, TypeVar

from fastapi import HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = Field(default=DEFAULT_PAGE, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    paginated: bool = False

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


def get_pagination_params(
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    paginated: bool = Query(default=False),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size, paginated=paginated)


def paginated_response(*, items: list[T], total: int, pagination: PaginationParams) -> dict[str, Any]:
    return {
        "items": items,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": total,
    }


def paginate_list(items: list[T], pagination: PaginationParams) -> tuple[list[T], int]:
    total = len(items)
    return items[pagination.offset : pagination.offset + pagination.page_size], total


def count_for_statement(db: Session, statement: Select[Any]) -> int:
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    return int(db.scalar(count_statement) or 0)


def paginate_statement(db: Session, statement: Select[Any], pagination: PaginationParams) -> tuple[list[Any], int]:
    total = count_for_statement(db, statement)
    page_statement = statement.offset(pagination.offset).limit(pagination.page_size)
    return list(db.scalars(page_statement).all()), total


def validate_sort(sort: str | None, *, allowed_fields: Iterable[str], default: str) -> str:
    sort_value = sort or default
    field = sort_value.removeprefix("-")
    allowed = set(allowed_fields)
    if field not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort field: {field}.",
        )
    return sort_value


def sort_descending(sort: str) -> bool:
    return sort.startswith("-")