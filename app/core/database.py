from collections.abc import Generator
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Amazon's CA bundle for every RDS region: https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
RDS_CA_BUNDLE = Path(__file__).resolve().parent.parent / "certs" / "rds-global-bundle.pem"


class Base(DeclarativeBase):
    pass


settings = get_settings()


@lru_cache
def _rds_client():  # type: ignore[no-untyped-def]
    import boto3

    return boto3.client("rds", region_name=settings.aws_region)


def _rds_auth_token(host: str, port: int, username: str) -> str:
    return _rds_client().generate_db_auth_token(
        DBHostname=host, Port=port, DBUsername=username, Region=settings.aws_region
    )


def create_database_engine(database_url: str, **kwargs: Any) -> Engine:
    url = make_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.host and url.host.endswith(".rds.amazonaws.com"):
        # RDS already rejects plain connections; verify-full also proves the server is the real instance.
        connect_args = {"sslmode": "verify-full", "sslrootcert": str(RDS_CA_BUNDLE)}

    engine = create_engine(url, connect_args=connect_args, **kwargs)

    if settings.database_iam_auth:
        # Tokens last 15 minutes and are only checked when a connection opens, so mint one per connection.
        @event.listens_for(engine, "do_connect")
        def _use_iam_token(dialect, conn_rec, cargs, cparams) -> None:
            cparams["password"] = _rds_auth_token(url.host, url.port or 5432, url.username)

    return engine


engine = create_database_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
