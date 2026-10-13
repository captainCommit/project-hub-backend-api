"""Sending notification email through an account's own Microsoft 365 mailbox, with SES as the fallback.

An email about one account goes out from that account's connected mailbox (Microsoft Graph sendMail), if it has
one. Anything else, or if the mailbox fails, goes through SES when SES is set up.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.account_member import AccountMember
from app.models.integration import ConnectionStatus, IntegrationConnection, IntegrationProvider
from app.services.email_sender import EmailSender, NoEmailRoute, OutgoingEmail
from app.services.integrations import microsoft
from app.services.integrations.http import Http, requests_http
from app.services.integrations.secrets import TokenCipher

logger = logging.getLogger(__name__)


def mailbox_for(db: Session, account_id: UUID | None) -> IntegrationConnection | None:
    if account_id is None:
        return None
    return db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.account_id == account_id,
            IntegrationConnection.provider == IntegrationProvider.MICROSOFT_MAIL.value,
            IntegrationConnection.status == ConnectionStatus.ACTIVE.value,
        )
    )


def can_send_for(db: Session, settings: Settings, account_id: UUID) -> bool:
    """Whether email about this account can be sent at all (SES, or the account's own mailbox)."""
    if settings.email_enabled:
        return True
    return bool(settings.email_token_secret and settings.app_url and mailbox_for(db, account_id) is not None)


def can_send_to(db: Session, settings: Settings, user_id: UUID) -> bool:
    """Whether email can reach this person about at least one of their accounts."""
    if settings.email_enabled:
        return True
    if not (settings.email_token_secret and settings.app_url):
        return False
    account_ids = db.scalars(select(AccountMember.account_id).where(AccountMember.user_id == user_id)).all()
    return any(mailbox_for(db, account_id) is not None for account_id in account_ids)


class RoutingEmailSender:
    def __init__(self, db: Session, settings: Settings, ses: EmailSender | None, *, http: Http = requests_http) -> None:
        self.db = db
        self.settings = settings
        self.ses = ses
        self.http = http

    def send(self, email: OutgoingEmail) -> None:
        connection = mailbox_for(self.db, email.account_id)
        if connection is not None:
            try:
                self.send_with_graph(connection, email)
                return
            except microsoft.GraphError as error:
                if error.needs_reconnect:
                    connection.status = ConnectionStatus.ERROR.value
                    connection.last_error = f"Microsoft 365 refused the mailbox: {error}"[:500]
                    self.db.commit()
                logger.warning("Microsoft 365 mail failed", extra={"account_id": str(email.account_id), "status": error.status})
                if self.ses is None:
                    raise
        if self.ses is None:
            raise NoEmailRoute
        self.ses.send(email)

    def send_with_graph(self, connection: IntegrationConnection, email: OutgoingEmail) -> None:
        cipher = TokenCipher(self.settings)
        tokens = microsoft.refresh(self.settings, self.http, "mail", cipher.decrypt(connection.account_id, connection.encrypted_token or b""))
        connection.encrypted_token = cipher.encrypt(connection.account_id, tokens.refresh_token)
        self.db.commit()
        microsoft.GraphClient(tokens.access_token, self.http).send_mail(to=email.to, subject=email.subject, html=email.html, headers=email.headers)
