from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol
from uuid import UUID

from app.core.config import Settings


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    headers: dict[str, str] = field(default_factory=dict)
    # The account the email is about, when it is about one; it may have its own Microsoft 365 mailbox to send from.
    account_id: UUID | None = None


class NoEmailRoute(Exception):
    """Nothing can send this email: SES isn't set up and the account has no Microsoft 365 mailbox connected."""


class EmailSender(Protocol):
    def send(self, email: OutgoingEmail) -> None: ...


def build_mime(email: OutgoingEmail, sender: str) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = email.to
    # Item titles are user input; a stray newline must not start a new header.
    message["Subject"] = " ".join(email.subject.split())
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].strip("> ") or None)
    for name, value in email.headers.items():
        message[name] = value
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    return message.as_bytes()


class SesEmailSender:
    def __init__(self, settings: Settings) -> None:
        import boto3

        self.settings = settings
        self.client = boto3.client("sesv2", region_name=settings.aws_region)

    def send(self, email: OutgoingEmail) -> None:
        request: dict[str, object] = {
            "FromEmailAddress": self.settings.email_from,
            "Destination": {"ToAddresses": [email.to]},
            "Content": {"Raw": {"Data": build_mime(email, self.settings.email_from)}},
        }
        if self.settings.ses_configuration_set:
            request["ConfigurationSetName"] = self.settings.ses_configuration_set
        self.client.send_email(**request)
