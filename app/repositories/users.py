from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email))

    def get_by_email_normalized(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(func.lower(User.email) == email.lower()))

    def get_by_cognito_sub(self, cognito_sub: str) -> User | None:
        return self.db.scalar(select(User).where(User.cognito_sub == cognito_sub))

    def create(
        self,
        *,
        email: str,
        full_name: str | None = None,
        cognito_sub: str | None = None,
    ) -> User:
        user = User(email=email, full_name=full_name, cognito_sub=cognito_sub)
        self.db.add(user)
        self.db.flush()
        self.db.refresh(user)
        return user

    def update_identity(
        self,
        user: User,
        *,
        email: str,
        full_name: str | None,
    ) -> User:
        user.email = email
        user.full_name = full_name
        self.db.add(user)
        self.db.flush()
        self.db.refresh(user)
        return user

    def update_email(self, user: User, *, email: str) -> User:
        user.email = email
        self.db.add(user)
        self.db.flush()
        self.db.refresh(user)
        return user