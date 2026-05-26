from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_member import AccountMember


class AccountRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, name: str, slug: str, created_by: UUID) -> Account:
        account = Account(name=name, slug=slug, created_by=created_by)
        self.db.add(account)
        self.db.flush()
        self.db.refresh(account)
        return account

    def get_by_id(self, account_id: UUID) -> Account | None:
        return self.db.get(Account, account_id)

    def list_for_user(self, user_id: UUID) -> list[Account]:
        statement = (
            select(Account)
            .join(AccountMember, AccountMember.account_id == Account.id)
            .where(AccountMember.user_id == user_id)
            .order_by(Account.created_at, Account.name)
        )
        return list(self.db.scalars(statement).all())

    def update(self, account: Account, *, name: str | None, slug: str | None) -> Account:
        if name is not None:
            account.name = name
        if slug is not None:
            account.slug = slug
        self.db.add(account)
        self.db.flush()
        self.db.refresh(account)
        return account