"""Requests Slack and Teams send us: Slack slash commands and events, and Teams bot activities.

The router checks each request's signature (Slack) or token (Teams) before anything here runs. Commands run as
the Project Hub user the sender linked; someone who hasn't linked yet is offered a link.
"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.integration import ConnectionStatus, IntegrationConnection, IntegrationProvider
from app.services.integrations.commands import CommandContext, help_message, run_command
from app.services.integrations.http import Http
from app.services.integrations.messages import ChatMessage, to_adaptive_card, to_slack_blocks
from app.services.integrations.service import IntegrationService, TeamsBotDirectory
from app.services.integrations.teams import TeamsClient, TeamsError

SLACK_PREFIX = "/projecthub"
TEAMS_PREFIX = ""
NOT_LINKED = "Link your Project Hub account first: {how}."
MENTION = re.compile(r"<at>.*?</at>", re.IGNORECASE)


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


# Teams --------------------------------------------------------------------------------------------------


def handle_teams_activity(db: Session, settings: Settings, http: Http, activity: dict[str, Any]) -> None:
    kind = activity.get("type")
    channel_data = activity.get("channelData") or {}
    tenant_id = str((channel_data.get("tenant") or {}).get("id") or (activity.get("conversation") or {}).get("tenantId") or "")
    service_url = str(activity.get("serviceUrl") or "")
    conversation = activity.get("conversation") or {}
    bot_id = (activity.get("recipient") or {}).get("id")
    directory = TeamsBotDirectory(db, settings, http)
    team = channel_data.get("team") or {}

    if kind in ("conversationUpdate", "installationUpdate"):
        added = any(member.get("id") == bot_id for member in activity.get("membersAdded") or []) or activity.get("action") == "add"
        removed = any(member.get("id") == bot_id for member in activity.get("membersRemoved") or []) or activity.get("action") == "remove"
        if added and team.get("id"):
            directory.sync_team_channels(tenant_id=tenant_id, team_id=team["id"], team_name=team.get("name"), service_url=service_url)
            send(settings, http, service_url, conversation.get("id"), "Hi! I'm Project Hub. An account owner can type **connect** and their code from Account > Integrations to link this organization; then choose channels for project updates in the app.")
        elif added and conversation.get("conversationType") == "personal":
            personal(directory, activity, tenant_id, service_url)
            db.commit()
            send(settings, http, service_url, conversation.get("id"), "Hi! Type **link** to connect your Project Hub account, then **help** to see what I can do.")
        elif removed and team.get("id"):
            directory.team_removed(team["id"])
        return

    if kind != "message":
        return
    text = MENTION.sub("", str(activity.get("text") or "")).strip()
    words = text.split()
    verb = words[0].lower() if words else ""
    reply_to = conversation.get("id")
    service = IntegrationService(db, settings, http=http)

    if verb == "connect" and len(words) >= 2:
        connection = service.use_teams_code(words[1], tenant_id=tenant_id, tenant_name=team.get("name") or (activity.get("from") or {}).get("name"))
        if team.get("id"):
            directory.sync_team_channels(tenant_id=tenant_id, team_id=team["id"], team_name=team.get("name"), service_url=service_url)
        send(settings, http, service_url, reply_to, "Connected. Owners can now pick this organization's channels in Project Hub." if connection else "That code isn't valid or has expired. Get a new one in Account > Integrations.")
        return

    sender = activity.get("from") or {}
    aad_id = str(sender.get("aadObjectId") or "")
    if verb in ("link", "login"):
        if conversation.get("conversationType") != "personal":
            send(settings, http, service_url, reply_to, "Send me **link** in a personal chat, so the link is only for you.")
            return
        chat = personal(directory, activity, tenant_id, service_url)
        db.commit()
        url = service.link_url(provider=IntegrationProvider.TEAMS, workspace_id=tenant_id, external_user_id=aad_id, name=sender.get("name"), conversation_id=chat.id)
        send_card(settings, http, service_url, reply_to, ChatMessage(
            title="Link your Project Hub account",
            lines=["Open this link while signed in to Project Hub. It works for 15 minutes and only for you."],
            link=url.removeprefix(settings.app_url.rstrip("/")),
            link_label="Link my account",
        ))
        return

    connections = connections_for(db, IntegrationProvider.TEAMS, tenant_id)
    for connection in connections:
        user = service.linked_user(connection, aad_id)
        if user is not None:
            message = help_message(TEAMS_PREFIX) if verb in ("", "help") else run_command(CommandContext(db, connection.account_id, user), text, prefix=TEAMS_PREFIX)
            send_card(settings, http, service_url, reply_to, message)
            return
    send(settings, http, service_url, reply_to, NOT_LINKED.format(how="send me **link** in a personal chat") if connections else "This organization isn't connected to Project Hub yet.")


def personal(directory: TeamsBotDirectory, activity: dict[str, Any], tenant_id: str, service_url: str) -> Any:
    sender = activity.get("from") or {}
    return directory.upsert(
        kind="PERSONAL",
        tenant_id=tenant_id,
        conversation_id=str((activity.get("conversation") or {}).get("id")),
        service_url=service_url,
        user_external_id=sender.get("aadObjectId"),
        name=sender.get("name"),
    )


def send(settings: Settings, http: Http, service_url: str, conversation_id: str | None, text: str) -> None:
    if not conversation_id:
        return
    try:
        TeamsClient(settings, http).send_text(service_url, conversation_id, text)
    except TeamsError:
        pass


def send_card(settings: Settings, http: Http, service_url: str, conversation_id: str | None, message: ChatMessage) -> None:
    if not conversation_id:
        return
    try:
        TeamsClient(settings, http).send_card(service_url, conversation_id, to_adaptive_card(message, settings), summary=message.title)
    except TeamsError:
        pass
