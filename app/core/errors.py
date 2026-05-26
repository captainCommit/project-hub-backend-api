from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_context import get_request_id


logger = logging.getLogger(__name__)

ERROR_CODE_BY_STATUS: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_SERVER_ERROR",
    status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}


def request_id_from_request(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or get_request_id()
    return str(request_id) if request_id else ""


def error_code_for_status(status_code: int) -> str:
    if status_code in ERROR_CODE_BY_STATUS:
        return ERROR_CODE_BY_STATUS[status_code]
    try:
        return HTTPStatus(status_code).phrase.upper().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "HTTP_ERROR"


def normalize_message(detail: Any, status_code: int) -> str:
    if isinstance(detail, str):
        return detail
    if detail is None:
        try:
            return HTTPStatus(status_code).phrase
        except ValueError:
            return "Request failed."
    if isinstance(detail, list):
        return "Validation error."
    return str(detail)


def error_payload(
    *,
    error_code: str,
    message: str,
    request_id: str,
    details: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error_code": error_code,
        "message": message,
        "request_id": request_id,
    }
    if details is not None:
        payload["details"] = details
    return payload


def error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(
            error_code=error_code,
            message=message,
            details=details,
            request_id=request_id,
        ),
        headers=headers,
    )


def validation_error_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for error in exc.errors():
        details.append(
            {
                "loc": list(error.get("loc", [])),
                "msg": error.get("msg"),
                "type": error.get("type"),
            }
        )
    return details


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = request_id_from_request(request)
    return error_response(
        status_code=exc.status_code,
        error_code=error_code_for_status(exc.status_code),
        message=normalize_message(exc.detail, exc.status_code),
        details=exc.detail if isinstance(exc.detail, (dict, list)) else None,
        request_id=request_id,
        headers=getattr(exc, "headers", None),
    )


async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = request_id_from_request(request)
    return error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_code="VALIDATION_ERROR",
        message="Validation error.",
        details=validation_error_details(exc),
        request_id=request_id,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_from_request(request)
    logger.exception(
        "Unhandled request error",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    return error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="INTERNAL_SERVER_ERROR",
        message="Internal server error.",
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)