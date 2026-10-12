"""Scheduled and event Lambda handlers for notification email (see serverless.yml).

Kept apart from app.main so these functions don't import FastAPI's routers on every cold start.
"""

import json
import logging
from typing import Any

import app.models  # noqa: F401  (registers every model with SQLAlchemy)
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.services.email_delivery import EmailDeliveryService
from app.services.email_sender import SesEmailSender

configure_logging()
logger = logging.getLogger(__name__)


def _service(db: Any, *, with_sender: bool = True) -> EmailDeliveryService:
    settings = get_settings()
    sender = SesEmailSender(settings) if with_sender and settings.email_enabled else None
    return EmailDeliveryService(db, settings, sender)


def deliver_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    """Every minute: send the emails people asked to get right away."""
    if not get_settings().email_enabled:
        return {}
    with SessionLocal() as db:
        counts = _service(db).deliver_pending()
    if any(counts.values()):
        logger.info("Notification emails delivered", extra=counts)
    return counts


def morning_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    """Hourly: for people whose local digest hour has come, create due-soon reminders and send the digest.

    Due-soon reminders are also in-app notifications, so they are created even when email isn't set up.
    """
    with SessionLocal() as db:
        counts = _service(db).run_morning()
    logger.info("Morning notifications run", extra=counts)
    return counts


def ses_events_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    """SNS messages from the SES configuration set: suppress hard-bounced and complaining addresses."""
    added = 0
    with SessionLocal() as db:
        service = _service(db, with_sender=False)
        for record in event.get("Records", []):
            try:
                message = json.loads(record["Sns"]["Message"])
            except (KeyError, TypeError, json.JSONDecodeError):
                logger.warning("Ignoring SES event that isn't JSON")
                continue
            added += service.record_ses_event(message)
    if added:
        logger.info("Email addresses suppressed", extra={"added": added})
    return {"suppressed": added}
