"""Requests Slack sends us: slash commands and events.

The router checks each request's Slack signature before anything here runs. Commands run as
the Project Hub user the sender linked; someone who hasn't linked yet is offered a link.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.integration import ConnectionStatus, IntegrationConnection, IntegrationProvider
from app.services.integrations.commands import CommandContext, help_message, run_command
from app.services.integrations.http import Http
from app.services.integrations.messages import ChatMessage, to_slack_blocks
from app.services.integrations.service import IntegrationService

SLACK_PREFIX = "/projecthub"
NOT_LINKED = "Link your Project Hub account first: {how}."


def connections_for(db: Session, provider: IntegrationProvider, external_id: str) -> list[IntegrationConnection]:
    return list(
        db.scalars(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == provider.value,
                IntegrationConnection.external_id == external_id,
                IntegrationConnection.status == ConnectionStatus.ACTIVE.value,
            )
        ).all()
    )


# Slack --------------------------------------------------------------------------------------------------


def slack_reply(message: ChatMessage, settings: Settings) -> dict[str, Any]:
    return {"response_type": "ephemeral", "text": message.plain_text(settings), "blocks": to_slack_blocks(message, settings)}


def handle_slack_command(db: Session, settings: Settings, http: Http, form: dict[str, str]) -> dict[str, Any]:
    team_id, user_id = form.get("team_id", ""), form.get("user_id", "")
    text = (form.get("text") or "").strip()
    connections = connections_for(db, IntegrationProvider.SLACK, team_id)
    if not connections:
        return slack_reply(ChatMessage(title="This Slack workspace isn't connected to Project Hub. An account owner can connect it in Account > Integrations."), settings)
    service = IntegrationService(db, settings, http=http)
    if text.lower() in ("link", "connect", "login"):
        url = service.link_url(provider=IntegrationProvider.SLACK, workspace_id=team_id, external_user_id=user_id, name=form.get("user_name"))
        return slack_reply(
            ChatMessage(
                title="Link your Project Hub account",
                lines=["Open this link while signed in to Project Hub. It works for 15 minutes and only for you."],
                link=url.removeprefix(settings.app_url.rstrip("/")),
                link_label="Link my account",
            ),
            settings,
        )
    for connection in connections:
        user = service.linked_user(connection, user_id)
        if user is not None:
            if not text or text.lower() == "help":
                return slack_reply(help_message(SLACK_PREFIX), settings)
            return slack_reply(run_command(CommandContext(db, connection.account_id, user), text, prefix=SLACK_PREFIX), settings)
    return slack_reply(ChatMessage(title=NOT_LINKED.format(how=f"type `{SLACK_PREFIX} link`")), settings)


def handle_slack_event(db: Session, settings: Settings, http: Http, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}
    event = body.get("event") or {}
    if event.get("type") in ("app_uninstalled", "tokens_revoked"):
        IntegrationService(db, settings, http=http).slack_uninstalled(str(body.get("team_id") or ""))
    return {}
