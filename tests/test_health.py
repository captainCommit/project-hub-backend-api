from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.routers import health as health_router


client = TestClient(app)


def override_get_db():
    yield MagicMock()


def test_root_endpoint() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Project Hub API is running"}


def test_swagger_ui_endpoint() -> None:
    response = client.get("/swagger")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Swagger UI" in response.text
    assert "http://testserver/swagger.json" in response.text


def test_swagger_json_endpoint_includes_api_paths_and_auth_scheme() -> None:
    response = client.get("/swagger.json")

    assert response.status_code == 200
    body = response.json()
    assert body["openapi"]
    assert body["info"]["title"] == "Project Hub API"
    assert "/api/v1/me" in body["paths"]
    assert "HTTPBearer" in body["components"]["securitySchemes"]


def test_health_endpoint_connected(monkeypatch) -> None:
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(health_router, "is_database_connected", lambda db: True)

    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_health_endpoint_degraded(monkeypatch) -> None:
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(health_router, "is_database_connected", lambda db: False)

    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "database": "disconnected"}