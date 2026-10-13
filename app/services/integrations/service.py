"""Managing integrations: what owners set up for the account, and what each person links for themselves.

Owners connect Slack (OAuth install), Teams (a one-time code typed to the bot) and a Microsoft 365 mailbox for
email, and choose which channels get which events. Everyone can link their own Slack or Teams user (started from
Slack or Teams, confirmed in the app while signed in, so both sides are proven), choose which notifications they
get as direct messages, and connect their calendar (see app.services.integrations.calendar).
"""

import json
import secrets as pysecrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.integration import (
    CHANNEL_EVENTS,
    ConnectionStatus,
    IntegrationConnection,
    IntegrationConversation,
    IntegrationDestination,
    IntegrationProvider,
    IntegrationState,
    IntegrationUserLink,
)
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.user import User
from app.services.access import access_for
from app.services.integrations import microsoft, slack
from app.services.integrations.http import Http, requests_http
from app.services.integrations.secrets import IntegrationNotConfigured, TokenCipher, platform_secret, sign, unsign
from app.services.integrations.slack import SlackClient, SlackError
from app.services.integrations.teams import TeamsClient, TeamsError
from app.services.notification_preferences import EMAIL_CATEGORY_DEFAULTS

STATE_TTL = 15 * 60
LINK_TTL = 15 * 60
TEAMS_CODE_TTL = timedelta(hours=1)
SETTINGS_PATH = "/Account?tab=integrations"
CONNECTED_APPS_PATH = "/Account?tab=connected"
DM_CATEGORIES = list(EMAIL_CATEGORY_DEFAULTS)
OWNERS_ONLY = "Only account owners manage integrations."


def not_configured(name: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{name} isn't set up for Project Hub yet.")


class IntegrationService:
    def __init__(self, db: Session, settings: Settings, *, http: Http = requests_http) -> None:
        self.db = db
        self.settings = settings
        self.http = http

    # Availability ----------------------------------------------------------------------------------------

    def available(self) -> dict[str, bool]:
        """Which integrations the platform has app credentials for."""
        return {
            "slack": bool(self.settings.slack_client_id and platform_secret(self.settings, "slack_client_secret")),
            "teams": bool(self.settings.teams_bot_app_id),
            "microsoft": bool(self.settings.microsoft_client_id and platform_secret(self.settings, "microsoft_client_secret")),
        }

    def require_owner(self, account_id: UUID, user: User) -> None:
        if not access_for(self.db, account_id=account_id, user=user).is_owner:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWNERS_ONLY)

    def connection(self, account_id: UUID, provider: IntegrationProvider) -> IntegrationConnection | None:
        return self.db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.account_id == account_id, IntegrationConnection.provider == provider.value
            )
        )

    def cipher(self) -> TokenCipher:
        try:
            return TokenCipher(self.settings)
        except IntegrationNotConfigured as exc:
            raise not_configured("Storing integration tokens") from exc

    # What owners see -------------------------------------------------------------------------------------

    def overview(self, *, account_id: UUID, current_user: User) -> dict[str, Any]:
        self.require_owner(account_id, current_user)
        connections = self.db.scalars(select(IntegrationConnection).where(IntegrationConnection.account_id == account_id)).all()
        destinations = self.db.scalars(
            select(IntegrationDestination).where(IntegrationDestination.account_id == account_id).order_by(IntegrationDestination.created_at)
        ).all()
        by_id = {c.id: c for c in connections}
        return {
            "available": self.available(),
            "connections": [self.present_connection(c) for c in connections],
            "destinations": [self.present_destination(d, by_id.get(d.connection_id)) for d in destinations],
            "events": [{"key": key, "label": label} for key, label in CHANNEL_EVENTS.items()],
        }

    def present_connection(self, connection: IntegrationConnection) -> dict[str, Any]:
        user = self.db.get(User, connection.connected_by) if connection.connected_by else None
        return {
            "provider": connection.provider,
            "status": connection.status,
            "external_name": connection.external_name,
            "last_error": connection.last_error,
            "connected_at": connection.created_at,
            "connected_by_name": (user.full_name or user.email) if user else None,
        }

    def present_destination(self, destination: IntegrationDestination, connection: IntegrationConnection | None) -> dict[str, Any]:
        scope_type, scope_id, model = next(
            (kind, value, model)
            for kind, value, model in (
                ("PROJECT", destination.project_id, Project),
                ("PROGRAM", destination.program_id, Program),
                ("PORTFOLIO", destination.portfolio_id, Portfolio),
            )
            if value
        )
        record = self.db.get(model, scope_id)
        creator = self.db.get(User, destination.created_by) if destination.created_by else None
        return {
            "id": destination.id,
            "provider": connection.provider if connection else None,
            "channel_id": destination.channel_id,
            "channel_name": destination.channel_name,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "scope_name": record.name if record else "",
            "events": destination.events or [],
            "is_active": destination.is_active,
            "created_by_name": (creator.full_name or creator.email) if creator else None,
        }

    def disconnect(self, *, account_id: UUID, provider: IntegrationProvider, current_user: User) -> None:
        self.require_owner(account_id, current_user)
        connection = self.connection(account_id, provider)
        if connection is None:
            return
        if provider == IntegrationProvider.SLACK and connection.encrypted_token and connection.status == ConnectionStatus.ACTIVE.value:
            try:
                SlackClient(self.cipher().decrypt(account_id, connection.encrypted_token), self.http).revoke()
            except (SlackError, HTTPException, ValueError):
                pass  # already revoked or unreachable; removing our copy is what matters
        self.db.delete(connection)
        self.db.commit()

    # Slack install ---------------------------------------------------------------------------------------

    def slack_install_url(self, *, account_id: UUID, current_user: User, nonce: str) -> str:
        self.require_owner(account_id, current_user)
        if not self.available()["slack"]:
            raise not_configured("Slack")
        state = sign(self.settings, "slack-install", {"a": str(account_id), "u": str(current_user.id), "n": nonce}, ttl_seconds=STATE_TTL)
        return slack.install_url(self.settings, state)

    def state_user(self, purpose: str, state: str, nonce: str | None) -> tuple[dict[str, Any], User]:
        """The signed state an OAuth provider sent back, and the person who started it.

        Session cookies are SameSite=Strict, so they don't come back with the provider's redirect. The state is
        signed and short-lived, and its nonce must match the cookie set in the browser that started, so a link
        someone else started can't be completed in your browser (or yours in theirs).
        """
        payload = unsign(self.settings, purpose, state)
        if payload is None or not nonce or payload.get("n") != nonce:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This sign-in link has expired. Start again from Project Hub.")
        user = self.db.get(User, UUID(payload["u"]))
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This sign-in link has expired. Start again from Project Hub.")
        return payload, user

    def slack_callback(self, *, code: str, state: str, nonce: str | None) -> UUID:
        payload, current_user = self.state_user("slack-install", state, nonce)
        account_id = UUID(payload["a"])
        self.require_owner(account_id, current_user)
        try:
            data = slack.exchange_code(self.settings, self.http, code)
        except SlackError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Slack didn't accept the install ({exc.error}).") from exc
        team = data.get("team") or {}
        connection = self.connection(account_id, IntegrationProvider.SLACK)
        if connection is not None and connection.external_id != team.get("id"):
            # A different workspace: what was set up for the old one doesn't apply.
            self.db.delete(connection)
            self.db.flush()
            connection = None
        if connection is None:
            connection = IntegrationConnection(account_id=account_id, provider=IntegrationProvider.SLACK.value, external_id=str(team.get("id")))
            self.db.add(connection)
        connection.external_name = team.get("name")
        connection.encrypted_token = self.cipher().encrypt(account_id, data["access_token"])
        connection.details = {"bot_user_id": data.get("bot_user_id"), "scope": data.get("scope"), "app_id": data.get("app_id")}
        connection.status = ConnectionStatus.ACTIVE.value
        connection.last_error = None
        connection.connected_by = current_user.id
        self.db.commit()
        return account_id

    def slack_uninstalled(self, team_id: str) -> None:
        for connection in self.db.scalars(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == IntegrationProvider.SLACK.value, IntegrationConnection.external_id == team_id
            )
        ).all():
            connection.status = ConnectionStatus.ERROR.value
            connection.last_error = "The Slack app was removed from the workspace."
        self.db.commit()

    # Channels and destinations ---------------------------------------------------------------------------

    def channels(self, *, account_id: UUID, provider: IntegrationProvider, current_user: User) -> list[dict[str, Any]]:
        self.require_owner(account_id, current_user)
        connection = self.require_connection(account_id, provider)
        if provider == IntegrationProvider.SLACK:
            try:
                client = SlackClient(self.cipher().decrypt(account_id, connection.encrypted_token or b""), self.http)
                return client.channels()
            except SlackError as exc:
                if exc.is_auth:
                    connection.status = ConnectionStatus.ERROR.value
                    connection.last_error = f"Slack refused the connection ({exc.error})."
                    self.db.commit()
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Slack didn't list the channels ({exc.error}).") from exc
        self.refresh_teams_channels(connection.external_id)
        conversations = self.db.scalars(
            select(IntegrationConversation)
            .where(
                IntegrationConversation.provider == IntegrationProvider.TEAMS.value,
                IntegrationConversation.tenant_id == connection.external_id,
                IntegrationConversation.kind == "CHANNEL",
                IntegrationConversation.is_active.is_(True),
            )
            .order_by(IntegrationConversation.team_name, IntegrationConversation.name)
        ).all()
        return [{"id": c.external_id, "name": f"{c.team_name or 'Team'} › {c.name or 'General'}", "is_private": False} for c in conversations]

    def refresh_teams_channels(self, tenant_id: str) -> None:
        """Ask Teams for each known team's channels, so channels added after the bot was installed show up."""
        teams = self.db.execute(
            select(IntegrationConversation.team_id, IntegrationConversation.team_name, IntegrationConversation.service_url)
            .where(
                IntegrationConversation.tenant_id == tenant_id,
                IntegrationConversation.kind == "CHANNEL",
                IntegrationConversation.is_active.is_(True),
                IntegrationConversation.team_id.is_not(None),
            )
            .distinct()
        ).all()
        for team_id, team_name, service_url in teams:
            try:
                TeamsBotDirectory(self.db, self.settings, self.http).sync_team_channels(
                    tenant_id=tenant_id, team_id=team_id, team_name=team_name, service_url=service_url
                )
            except TeamsError:
                continue

    def require_connection(self, account_id: UUID, provider: IntegrationProvider) -> IntegrationConnection:
        connection = self.connection(account_id, provider)
        if connection is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connect it first.")
        return connection

    def save_destination(self, *, account_id: UUID, current_user: User, values: dict[str, Any], destination_id: UUID | None = None) -> dict[str, Any]:
        self.require_owner(account_id, current_user)
        provider = IntegrationProvider(values["provider"])
        connection = self.require_connection(account_id, provider)
        events = [event for event in dict.fromkeys(values.get("events") or []) if event in CHANNEL_EVENTS]
        if not events:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose at least one event.")
        scope_type, scope_id = values["scope_type"], values["scope_id"]
        model = {"PROJECT": Project, "PROGRAM": Program, "PORTFOLIO": Portfolio}[scope_type]
        record = self.db.get(model, scope_id)
        if record is None or record.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{scope_type.title()} not found.")
        if provider == IntegrationProvider.TEAMS:
            known = self.db.scalar(
                select(IntegrationConversation).where(
                    IntegrationConversation.external_id == values["channel_id"], IntegrationConversation.tenant_id == connection.external_id
                )
            )
            if known is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Add the Project Hub app to that team first.")

        if destination_id is None:
            destination = IntegrationDestination(account_id=account_id, created_by=current_user.id)
            self.db.add(destination)
        else:
            destination = self.get_destination(account_id, destination_id)
        destination.connection_id = connection.id
        destination.channel_id = values["channel_id"]
        destination.channel_name = values["channel_name"][:255]
        destination.portfolio_id = scope_id if scope_type == "PORTFOLIO" else None
        destination.program_id = scope_id if scope_type == "PROGRAM" else None
        destination.project_id = scope_id if scope_type == "PROJECT" else None
        destination.events = events
        destination.is_active = bool(values.get("is_active", True))
        # Whoever saves it last is who it is checked against from now on.
        destination.created_by = current_user.id
        self.db.commit()
        return self.present_destination(destination, connection)

    def get_destination(self, account_id: UUID, destination_id: UUID) -> IntegrationDestination:
        destination = self.db.get(IntegrationDestination, destination_id)
        if destination is None or destination.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found.")
        return destination

    def delete_destination(self, *, account_id: UUID, destination_id: UUID, current_user: User) -> None:
        self.require_owner(account_id, current_user)
        self.db.delete(self.get_destination(account_id, destination_id))
        self.db.commit()

    # Teams connect code ----------------------------------------------------------------------------------

    def teams_connect_code(self, *, account_id: UUID, current_user: User) -> dict[str, Any]:
        """A short code an owner types to the bot in Teams, which ties their Microsoft tenant to this account."""
        self.require_owner(account_id, current_user)
        if not self.available()["teams"]:
            raise not_configured("Microsoft Teams")
        code = "".join(pysecrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        expires = datetime.now(UTC) + TEAMS_CODE_TTL
        self.db.add(
            IntegrationState(
                key=f"teams-code:{code}",
                value=json.dumps({"account_id": str(account_id), "user_id": str(current_user.id), "expires": expires.isoformat()}),
            )
        )
        self.db.execute(delete(IntegrationState).where(IntegrationState.key.like("teams-code:%"), IntegrationState.updated_at < datetime.now(UTC) - timedelta(days=1)))
        self.db.commit()
        return {"code": code, "expires_at": expires}

    def use_teams_code(self, code: str, *, tenant_id: str, tenant_name: str | None) -> IntegrationConnection | None:
        state = self.db.get(IntegrationState, f"teams-code:{code.strip().upper()}")
        if state is None:
            return None
        data = json.loads(state.value)
        self.db.delete(state)
        if datetime.fromisoformat(data["expires"]) < datetime.now(UTC):
            self.db.commit()
            return None
        account_id = UUID(data["account_id"])
        connection = self.connection(account_id, IntegrationProvider.TEAMS)
        if connection is not None and connection.external_id != tenant_id:
            self.db.delete(connection)
            self.db.flush()
            connection = None
        if connection is None:
            connection = IntegrationConnection(account_id=account_id, provider=IntegrationProvider.TEAMS.value, external_id=tenant_id)
            self.db.add(connection)
        connection.external_name = tenant_name or connection.external_name or "Microsoft Teams"
        connection.status = ConnectionStatus.ACTIVE.value
        connection.last_error = None
        connection.connected_by = UUID(data["user_id"])
        self.db.commit()
        return connection

    # Personal links (direct messages and commands) -------------------------------------------------------

    def link_url(self, *, provider: IntegrationProvider, workspace_id: str, external_user_id: str, name: str | None, conversation_id: UUID | None = None) -> str:
        """The link a Slack or Teams user opens, signed in, to link themselves to their Project Hub user."""
        token = sign(
            self.settings,
            "user-link",
            {"p": provider.value, "w": workspace_id, "x": external_user_id, "n": name, "c": str(conversation_id) if conversation_id else None},
            ttl_seconds=LINK_TTL,
        )
        return f"{self.settings.app_url.rstrip('/')}{CONNECTED_APPS_PATH}&link={token}"

    def confirm_link(self, *, account_id: UUID, token: str, current_user: User) -> dict[str, Any]:
        access_for(self.db, account_id=account_id, user=current_user)
        payload = unsign(self.settings, "user-link", token)
        if payload is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This link has expired. Ask for a new one in Slack or Teams.")
        provider = IntegrationProvider(payload["p"])
        connection = self.connection(account_id, provider)
        if connection is None or connection.external_id != payload["w"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"This account isn't connected to that {'Slack workspace' if provider == IntegrationProvider.SLACK else 'Teams organization'}. Switch to the right account and open the link again.",
            )
        self.db.execute(
            delete(IntegrationUserLink).where(
                IntegrationUserLink.connection_id == connection.id,
                (IntegrationUserLink.user_id == current_user.id) | (IntegrationUserLink.external_user_id == payload["x"]),
            )
        )
        link = IntegrationUserLink(
            account_id=account_id,
            connection_id=connection.id,
            user_id=current_user.id,
            external_user_id=payload["x"],
            external_name=payload.get("n"),
            conversation_id=UUID(payload["c"]) if payload.get("c") else None,
        )
        self.db.add(link)
        self.db.commit()
        return self.present_link(link, connection)

    def linked_user(self, connection: IntegrationConnection, external_user_id: str) -> User | None:
        link = self.db.scalar(
            select(IntegrationUserLink).where(
                IntegrationUserLink.connection_id == connection.id, IntegrationUserLink.external_user_id == external_user_id
            )
        )
        user = self.db.get(User, link.user_id) if link else None
        return user if user is not None and user.is_active else None

    def my_links(self, *, account_id: UUID, current_user: User) -> dict[str, Any]:
        access_for(self.db, account_id=account_id, user=current_user)
        result: dict[str, Any] = {"available": self.available(), "dm_categories": DM_CATEGORIES}
        for provider in (IntegrationProvider.SLACK, IntegrationProvider.TEAMS):
            connection = self.connection(account_id, provider)
            link = (
                self.db.scalar(
                    select(IntegrationUserLink).where(
                        IntegrationUserLink.connection_id == connection.id, IntegrationUserLink.user_id == current_user.id
                    )
                )
                if connection
                else None
            )
            result[provider.value.lower()] = {
                "connected": connection is not None and connection.status == ConnectionStatus.ACTIVE.value,
                "workspace": connection.external_name if connection else None,
                "link": self.present_link(link, connection) if link and connection else None,
            }
        return result

    def present_link(self, link: IntegrationUserLink, connection: IntegrationConnection) -> dict[str, Any]:
        return {
            "provider": connection.provider,
            "external_name": link.external_name,
            "dm_categories": link.dm_categories or [],
            "linked_at": link.created_at,
        }

    def update_link(self, *, account_id: UUID, provider: IntegrationProvider, current_user: User, dm_categories: list[str]) -> dict[str, Any]:
        connection = self.require_connection(account_id, provider)
        link = self.db.scalar(
            select(IntegrationUserLink).where(IntegrationUserLink.connection_id == connection.id, IntegrationUserLink.user_id == current_user.id)
        )
        if link is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not linked.")
        link.dm_categories = [category for category in dict.fromkeys(dm_categories) if category in DM_CATEGORIES]
        self.db.commit()
        return self.present_link(link, connection)

    def unlink(self, *, account_id: UUID, provider: IntegrationProvider, current_user: User) -> None:
        connection = self.connection(account_id, provider)
        if connection is not None:
            self.db.execute(
                delete(IntegrationUserLink).where(IntegrationUserLink.connection_id == connection.id, IntegrationUserLink.user_id == current_user.id)
            )
            self.db.commit()

    # Microsoft sign-in (calendar for a person, mailbox for an account) -----------------------------------

    def microsoft_url(self, *, purpose: str, current_user: User, nonce: str, account_id: UUID | None = None) -> str:
        if not self.available()["microsoft"]:
            raise not_configured("Microsoft 365")
        if purpose == "mail":
            assert account_id is not None
            self.require_owner(account_id, current_user)
        state = sign(
            self.settings,
            "microsoft",
            {"k": purpose, "u": str(current_user.id), "a": str(account_id) if account_id else None, "n": nonce},
            ttl_seconds=STATE_TTL,
        )
        return microsoft.authorize_url(self.settings, purpose, state)

    def microsoft_callback(self, *, code: str, state: str, nonce: str | None) -> str:
        """Finish a Microsoft sign-in. Returns the app path to go back to."""
        payload, current_user = self.state_user("microsoft", state, nonce)
        purpose = payload["k"]
        try:
            tokens = microsoft.exchange_code(self.settings, self.http, purpose, code)
            me = microsoft.GraphClient(tokens.access_token, self.http).me()
        except microsoft.GraphError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Microsoft sign-in failed: {exc}") from exc
        address = me.get("mail") or me.get("userPrincipalName")
        if purpose == "calendar":
            from app.services.integrations.calendar import CalendarSyncService

            CalendarSyncService(self.db, self.settings, http=self.http).connect(current_user, me_id=str(me["id"]), email=address, tokens=tokens)
            return f"{CONNECTED_APPS_PATH}&connected=outlook"

        account_id = UUID(payload["a"])
        self.require_owner(account_id, current_user)
        connection = self.connection(account_id, IntegrationProvider.MICROSOFT_MAIL)
        if connection is None:
            connection = IntegrationConnection(account_id=account_id, provider=IntegrationProvider.MICROSOFT_MAIL.value, external_id=str(me["id"]))
            self.db.add(connection)
        connection.external_id = str(me["id"])
        connection.external_name = address
        connection.encrypted_token = self.cipher().encrypt(account_id, tokens.refresh_token)
        connection.status = ConnectionStatus.ACTIVE.value
        connection.last_error = None
        connection.connected_by = current_user.id
        self.db.commit()
        return f"{SETTINGS_PATH}&connected=microsoft-mail"


class TeamsBotDirectory:
    """What the bot knows about where it is installed: teams, their channels, and personal chats."""

    def __init__(self, db: Session, settings: Settings, http: Http) -> None:
        self.db = db
        self.settings = settings
        self.http = http

    def upsert(self, *, kind: str, tenant_id: str, conversation_id: str, service_url: str, **fields: Any) -> IntegrationConversation:
        conversation = self.db.scalar(
            select(IntegrationConversation).where(
                IntegrationConversation.provider == IntegrationProvider.TEAMS.value, IntegrationConversation.external_id == conversation_id
            )
        )
        if conversation is None:
            conversation = IntegrationConversation(provider=IntegrationProvider.TEAMS.value, kind=kind, external_id=conversation_id)
            self.db.add(conversation)
        conversation.tenant_id = tenant_id
        conversation.service_url = service_url
        conversation.is_active = True
        for name, value in fields.items():
            if value is not None:
                setattr(conversation, name, value)
        self.db.flush()
        return conversation

    def sync_team_channels(self, *, tenant_id: str, team_id: str, team_name: str | None, service_url: str) -> None:
        from app.services.integrations.teams import bot_token, service_url_allowed

        if not service_url_allowed(service_url):
            return
        response = self.http(
            "GET",
            f"{service_url.rstrip('/')}/v3/teams/{team_id}/conversations",
            headers={"Authorization": f"Bearer {bot_token(self.settings, self.http)}"},
        )
        if not response.ok or not isinstance(response.data, dict):
            raise TeamsError("Teams didn't list the channels.", status=response.status)
        for channel in response.data.get("conversations") or []:
            self.upsert(
                kind="CHANNEL",
                tenant_id=tenant_id,
                conversation_id=channel["id"],
                service_url=service_url,
                team_id=team_id,
                team_name=team_name,
                name=channel.get("name") or "General",
            )
        self.db.commit()

    def team_removed(self, team_id: str) -> None:
        for conversation in self.db.scalars(select(IntegrationConversation).where(IntegrationConversation.team_id == team_id)).all():
            conversation.is_active = False
        self.db.commit()


__all__ = ["IntegrationService", "TeamsBotDirectory", "TeamsClient"]
