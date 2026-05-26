from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4


REQUEST_ID_HEADER = "X-Request-ID"

_request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


def generate_request_id() -> str:
    return str(uuid4())


def get_request_id() -> str | None:
    return _request_id_context.get()


def set_request_id(request_id: str) -> object:
    return _request_id_context.set(request_id)


def reset_request_id(token: object) -> None:
    _request_id_context.reset(token)  # type: ignore[arg-type]