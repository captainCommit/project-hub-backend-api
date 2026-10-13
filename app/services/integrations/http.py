"""The one place integrations make HTTP calls, so tests can stand in for Slack and Microsoft."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    data: Any  # parsed JSON, or the text if the body isn't JSON

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


# (method, url, headers, json body, form body, query params, timeout)
Http = Callable[..., HttpResponse]


def requests_http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    data: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> HttpResponse:
    import requests

    response = requests.request(method, url, headers=headers, json=json, data=data, params=params, timeout=timeout)
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return HttpResponse(response.status_code, body)
