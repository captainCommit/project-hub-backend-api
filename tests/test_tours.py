from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app
from tests.ai_support import db_session  # noqa: F401


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="local", environment="local")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_seen_tours_are_remembered_once_and_can_be_reset(client: TestClient) -> None:
    assert client.get("/api/v1/me").json()["seen_tours"] == []
    assert client.post("/api/v1/me/tours/raid").json()["seen_tours"] == ["raid"]
    assert client.post("/api/v1/me/tours/home").json()["seen_tours"] == ["raid", "home"]
    assert client.post("/api/v1/me/tours/raid").json()["seen_tours"] == ["raid", "home"]
    assert client.get("/api/v1/me").json()["seen_tours"] == ["raid", "home"]
    assert client.delete("/api/v1/me/tours").json()["seen_tours"] == []


def test_tour_ids_are_checked(client: TestClient) -> None:
    assert client.post("/api/v1/me/tours/Not%20A%20Tour").status_code == 400
