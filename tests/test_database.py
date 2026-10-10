import pytest

from app.core import database

RDS_URL = "postgresql+psycopg://project_hub_app@project-hub-db.abc123.ca-central-1.rds.amazonaws.com:5432/project_hub"


class _Captured(Exception):
    pass


def _connect_params(engine, monkeypatch) -> dict:
    """The keyword arguments the engine would hand to psycopg.connect, without opening a connection."""
    captured = {}

    def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        raise _Captured

    monkeypatch.setattr(engine.dialect, "connect", fake_connect)
    with pytest.raises(Exception) as excinfo:
        engine.connect()
    assert isinstance(excinfo.value, _Captured) or isinstance(excinfo.value.__cause__, _Captured)
    return captured


def test_rds_connections_verify_the_server_certificate(monkeypatch):
    monkeypatch.setattr(database.settings, "database_iam_auth", False)
    engine = database.create_database_engine(RDS_URL)

    params = _connect_params(engine, monkeypatch)

    assert params["sslmode"] == "verify-full"
    assert params["sslrootcert"] == str(database.RDS_CA_BUNDLE)
    assert database.RDS_CA_BUNDLE.is_file()
    assert "password" not in params


def test_iam_auth_mints_a_token_per_connection(monkeypatch):
    monkeypatch.setattr(database.settings, "database_iam_auth", True)
    calls = []

    def fake_token(host, port, username):
        calls.append((host, port, username))
        return f"token-{len(calls)}"

    monkeypatch.setattr(database, "_rds_auth_token", fake_token)
    engine = database.create_database_engine(RDS_URL)

    assert _connect_params(engine, monkeypatch)["password"] == "token-1"
    assert _connect_params(engine, monkeypatch)["password"] == "token-2"
    assert calls[0] == ("project-hub-db.abc123.ca-central-1.rds.amazonaws.com", 5432, "project_hub_app")


def test_local_connections_are_unchanged(monkeypatch):
    monkeypatch.setattr(database.settings, "database_iam_auth", False)
    engine = database.create_database_engine("postgresql+psycopg://postgres:postgres@localhost:5432/project_hub")

    params = _connect_params(engine, monkeypatch)

    assert params["password"] == "postgres"
    assert "sslmode" not in params
