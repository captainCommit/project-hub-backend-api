"""Scheduled and event Lambda handlers: notification email, Slack/Teams delivery, calendars, AI (see serverless.yml).

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
    """Email goes out from the account's own Microsoft 365 mailbox if it connected one, else through SES."""
    from app.services.integrations.mail import RoutingEmailSender

    settings = get_settings()
    ses = SesEmailSender(settings) if with_sender and settings.email_enabled else None
    sender = RoutingEmailSender(db, settings, ses) if with_sender else None
    return EmailDeliveryService(db, settings, sender)


def deliver_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    """Every minute: send the emails people asked to get right away."""
    settings = get_settings()
    # Without SES, only accounts with their own Microsoft 365 mailbox can send, and links still need signing.
    if not (settings.email_enabled or (settings.email_token_secret and settings.app_url)):
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
    from app.services.integrations.events import queue_overdue_summaries

    with SessionLocal() as db:
        counts = _service(db).run_morning()
        counts["overdue_summaries"] = queue_overdue_summaries(db)
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


def ai_run_handler(event: dict[str, Any], context: Any) -> dict[str, str]:
    """Started asynchronously by the API for each question or report: answers or writes it (app.services.ai.runner).

    Runs as AiWorkerRole, the only role that can decrypt accounts' own model keys.
    """
    from uuid import UUID

    from app.services.ai.runner import AssistantRunner

    run_id = UUID(str(event["run_id"]))
    with SessionLocal() as db:
        outcome = AssistantRunner(db, get_settings()).run(run_id)
    return {"run_id": str(run_id), "status": outcome}


def ai_housekeeping_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    """Hourly: mark runs whose worker died as failed, delete conversations past their account's retention, and
    report platform AI usage to Stripe."""
    from app.services.ai.conversations import ConversationService
    from app.services.ai.usage_billing import AiUsageReporter

    settings = get_settings()
    with SessionLocal() as db:
        service = ConversationService(db, settings)
        counts = {"stale_runs": service.fail_stale_runs(), "expired_conversations": service.delete_expired()}
        usage = AiUsageReporter(db, settings).report_all()
    counts.update({f"usage_{key}": value for key, value in usage.items()})
    logger.info("AI housekeeping", extra=counts)
    return counts


def integrations_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    """Every minute: queue channel updates from new activity, then send what's queued to Slack and Teams."""
    from app.services.integrations.delivery import IntegrationDelivery
    from app.services.integrations.events import EventScanner

    with SessionLocal() as db:
        queued = EventScanner(db).scan()
        counts = IntegrationDelivery(db, get_settings()).deliver_pending()
    counts["queued"] = queued
    if any(counts.values()):
        logger.info("Integration messages", extra=counts)
    return counts


def calendar_sync_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    """Every 15 minutes: bring connected Outlook calendars up to date with people's due dates and milestones."""
    from app.services.integrations.calendar import CalendarSyncService

    with SessionLocal() as db:
        counts = CalendarSyncService(db, get_settings()).sync_all()
    if any(counts.values()):
        logger.info("Outlook calendars synced", extra=counts)
    return counts
