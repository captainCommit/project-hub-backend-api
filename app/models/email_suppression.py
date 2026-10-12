from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailSuppression(Base):
    """An address SES reported as a hard bounce or a spam complaint. Nothing more is emailed to it."""

    __tablename__ = "email_suppressions"
    __table_args__ = (
        CheckConstraint("reason in ('BOUNCE', 'COMPLAINT')", name="ck_email_suppressions_reason"),
    )

    # Stored lower-case.
    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
