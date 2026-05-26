from httpx import Response


def assert_error_response(
    response: Response,
    *,
    status_code: int,
    error_code: str,
    message: str,
) -> dict[str, object]:
    assert response.status_code == status_code
    body = response.json()
    assert body["error_code"] == error_code
    assert body["message"] == message
    assert body["request_id"]
    assert "detail" not in body
    return body