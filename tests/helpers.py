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

def grant_access(
    db_session: "Session",
    *,
    account_id: "str | UUID",
    role: str,
    portfolio_id: "str | UUID | None" = None,
    program_id: "str | UUID | None" = None,
    project_id: "str | UUID | None" = None,
    modules: list[str] | None = None,
    email: str = "dev@example.com",
    view_modules: list[str] | None = None,
) -> None:
    """Give a user (the local dev user by default) a role on one portfolio, program or project."""
    from uuid import UUID

    from sqlalchemy import select

    from app.models.access_grant import AccessGrant
    from app.models.user import User

    def as_uuid(value: "str | UUID | None") -> "UUID | None":
        return UUID(str(value)) if value is not None else None

    user = db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    db_session.add(
        AccessGrant(
            account_id=as_uuid(account_id),
            user_id=user.id,
            portfolio_id=as_uuid(portfolio_id),
            program_id=as_uuid(program_id),
            project_id=as_uuid(project_id),
            role=role,
            modules=modules,
            view_modules=view_modules,
        )
    )
    db_session.commit()


def grant_existing_portfolios(db_session: "Session", account_id: "str | UUID", role: object) -> None:
    """Mirror the access backfill for the dev user: their role on every portfolio in the account.

    Tests that change the dev user's account role keep the access they had before project grants
    existed; tests about narrower access add their own grants with grant_access.
    """
    from uuid import UUID

    from sqlalchemy import delete, select

    from app.models.access_grant import ALL_MODULES, AccessGrant
    from app.models.portfolio import Portfolio
    from app.models.user import User

    account_uuid = UUID(str(account_id))
    role_value = getattr(role, "value", role)
    user = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    assert user is not None
    db_session.execute(
        delete(AccessGrant).where(AccessGrant.account_id == account_uuid, AccessGrant.user_id == user.id)
    )
    if role_value != "OWNER":
        for portfolio_id in db_session.scalars(select(Portfolio.id).where(Portfolio.account_id == account_uuid)):
            db_session.add(
                AccessGrant(
                    account_id=account_uuid,
                    user_id=user.id,
                    portfolio_id=portfolio_id,
                    role=role_value,
                    modules=list(ALL_MODULES) if role_value == "MEMBER" else None,
                )
            )
    db_session.commit()
