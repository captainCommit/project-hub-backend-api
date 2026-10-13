"""Slack, Microsoft Teams and Outlook: owners' settings, each person's links and calendar, and the endpoints the
providers call (OAuth redirects, Slack commands and events, Teams bot messages, calendar feeds)."""

import json
import secrets
from typing import Any, Literal
from urllib.parse import parse_qsl, quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.integration import CHANNEL_EVENTS, IntegrationProvider
from app.models.user import User
from app.services.auth import get_current_user
from app.services.integrations import slack, teams
from app.services.integrations.calendar import CalendarFeedService, CalendarSyncService
from app.services.integrations.http import Http, requests_http
from app.services.integrations.inbound import handle_slack_command, handle_slack_event, handle_teams_activity
from app.services.integrations.secrets import IntegrationNotConfigured
from app.services.integrations.service import DM_CATEGORIES, SETTINGS_PATH, IntegrationService
from app.services.integrations.teams_package import build_package

router = APIRouter(prefix="/api/v1", tags=["integrations"])

OAUTH_COOKIE = "ph_integration_oauth"
OAUTH_COOKIE_SECONDS = 15 * 60
PROVIDER_PATHS: dict[str, IntegrationProvider] = {
    "slack": IntegrationProvider.SLACK,
    "teams": IntegrationProvider.TEAMS,
    "microsoft-mail": IntegrationProvider.MICROSOFT_MAIL,
}
ProviderPath = Literal["slack", "teams", "microsoft-mail"]
ChatProviderPath = Literal["slack", "teams"]
ScopeType = Literal["PROJECT", "PROGRAM", "PORTFOLIO"]


# Overridden in tests so no request leaves the machine.
def get_integrations_http() -> Http:
    return requests_http


def integration_service(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings), http: Http = Depends(get_integrations_http)
) -> IntegrationService:
    return IntegrationService(db, settings, http=http)


def start_oauth(response: Response, settings: Settings) -> str:
    """A nonce for the OAuth state, also kept in a short-lived cookie that comes back with the provider's redirect."""
    nonce = secrets.token_urlsafe(24)
    response.set_cookie(
        OAUTH_COOKIE,
        nonce,
        max_age=OAUTH_COOKIE_SECONDS,
        httponly=True,
        secure=settings.environment != "local",
        samesite="lax",
        path="/api/v1/integrations",
    )
    return nonce


def back_to_app(settings: Settings, path: str, *, error: str | None = None) -> RedirectResponse:
    target = settings.app_url.rstrip("/") + path
    if error:
        target += ("&" if "?" in target else "?") + "integration_error=" + quote(error[:200])
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(OAUTH_COOKIE, path="/api/v1/integrations")
    return response


# Owners: the account's integrations ---------------------------------------------------------------------


class DestinationIn(BaseModel):
    provider: ChatProviderPath
    channel_id: str = Field(min_length=1, max_length=300)
    channel_name: str = Field(min_length=1, max_length=255)
    scope_type: ScopeType
    scope_id: UUID
    events: list[str] = Field(min_length=1)
    is_active: bool = True


def destination_values(destination: DestinationIn) -> dict[str, Any]:
    values = destination.model_dump()
    values["provider"] = PROVIDER_PATHS[destination.provider].value
    return values


@router.get("/accounts/{account_id}/integrations", summary="The account's integrations and channels (owners)")
def get_integrations(
    account_id: UUID, service: IntegrationService = Depends(integration_service), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    return service.overview(account_id=account_id, current_user=current_user)


@router.get("/accounts/{account_id}/integrations/slack/install", summary="Where to send an owner to install the Slack app")
def slack_install(
    account_id: UUID,
    response: Response,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    return {"url": service.slack_install_url(account_id=account_id, current_user=current_user, nonce=start_oauth(response, service.settings))}


@router.get("/accounts/{account_id}/integrations/microsoft-mail/connect", summary="Where to send an owner to connect a Microsoft 365 mailbox")
def microsoft_mail_connect(
    account_id: UUID,
    response: Response,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    nonce = start_oauth(response, service.settings)
    return {"url": service.microsoft_url(purpose="mail", current_user=current_user, nonce=nonce, account_id=account_id)}


@router.post("/accounts/{account_id}/integrations/teams/code", summary="A one-time code that links a Teams organization (owners)")
def teams_code(
    account_id: UUID, service: IntegrationService = Depends(integration_service), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    return service.teams_connect_code(account_id=account_id, current_user=current_user)


@router.get("/accounts/{account_id}/integrations/teams/app-package", summary="The Teams app package to upload to Teams (owners)")
def teams_app_package(
    account_id: UUID, service: IntegrationService = Depends(integration_service), current_user: User = Depends(get_current_user)
) -> Response:
    service.require_owner(account_id, current_user)
    if not service.available()["teams"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Microsoft Teams isn't set up for Project Hub yet.")
    return Response(
        build_package(service.settings),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="project-hub-teams.zip"'},
    )


@router.delete("/accounts/{account_id}/integrations/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect (owners)")
def disconnect(
    account_id: UUID,
    provider: ProviderPath,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> Response:
    service.disconnect(account_id=account_id, provider=PROVIDER_PATHS[provider], current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/accounts/{account_id}/integrations/{provider}/channels", summary="Channels that can receive updates (owners)")
def list_channels(
    account_id: UUID,
    provider: ChatProviderPath,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return service.channels(account_id=account_id, provider=PROVIDER_PATHS[provider], current_user=current_user)


@router.post("/accounts/{account_id}/integrations/destinations", status_code=status.HTTP_201_CREATED, summary="Send updates to a channel (owners)")
def create_destination(
    account_id: UUID,
    destination: DestinationIn,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return service.save_destination(account_id=account_id, current_user=current_user, values=destination_values(destination))


@router.put("/accounts/{account_id}/integrations/destinations/{destination_id}", summary="Change a channel's updates (owners)")
def update_destination(
    account_id: UUID,
    destination_id: UUID,
    destination: DestinationIn,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return service.save_destination(
        account_id=account_id, current_user=current_user, values=destination_values(destination), destination_id=destination_id
    )


@router.delete("/accounts/{account_id}/integrations/destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Stop sending updates to a channel (owners)")
def delete_destination(
    account_id: UUID,
    destination_id: UUID,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> Response:
    service.delete_destination(account_id=account_id, destination_id=destination_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Everyone: their own links and calendar ----------------------------------------------------------------


class LinkConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=2000)


class LinkUpdate(BaseModel):
    dm_categories: list[str]


@router.get("/accounts/{account_id}/integrations/me", summary="Your Slack and Teams links and calendar connections")
def my_integrations(
    account_id: UUID,
    db: Session = Depends(get_db),
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    result = service.my_links(account_id=account_id, current_user=current_user)
    result["calendar_feed"] = CalendarFeedService(db, service.settings).read(current_user) if _feeds_available(service.settings) else None
    result["outlook_calendar"] = CalendarSyncService(db, service.settings, http=service.http).read(current_user)
    result["dm_category_options"] = DM_CATEGORIES
    return result


def _feeds_available(settings: Settings) -> bool:
    from app.services.integrations.secrets import platform_secret

    return bool(platform_secret(settings, "integration_state_secret"))


@router.post("/accounts/{account_id}/integrations/me/links/confirm", summary="Link the Slack or Teams user who started it to you")
def confirm_link(
    account_id: UUID,
    body: LinkConfirm,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return service.confirm_link(account_id=account_id, token=body.token, current_user=current_user)


@router.patch("/accounts/{account_id}/integrations/me/links/{provider}", summary="Choose which notifications come as direct messages")
def update_link(
    account_id: UUID,
    provider: ChatProviderPath,
    body: LinkUpdate,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return service.update_link(account_id=account_id, provider=PROVIDER_PATHS[provider], current_user=current_user, dm_categories=body.dm_categories)


@router.delete("/accounts/{account_id}/integrations/me/links/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Unlink your Slack or Teams user")
def unlink(
    account_id: UUID,
    provider: ChatProviderPath,
    service: IntegrationService = Depends(integration_service),
    current_user: User = Depends(get_current_user),
) -> Response:
    service.unlink(account_id=account_id, provider=PROVIDER_PATHS[provider], current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/calendar-feed", summary="Turn on your calendar feed, or replace its URL (the old one stops working)")
def create_calendar_feed(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    return CalendarFeedService(db, settings).create(current_user)


@router.delete("/me/calendar-feed", status_code=status.HTTP_204_NO_CONTENT, summary="Turn off your calendar feed")
def delete_calendar_feed(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings), current_user: User = Depends(get_current_user)
) -> Response:
    CalendarFeedService(db, settings).delete(current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/outlook-calendar/connect", summary="Where to send you to connect your Outlook calendar")
def outlook_calendar_connect(
    response: Response, service: IntegrationService = Depends(integration_service), current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    return {"url": service.microsoft_url(purpose="calendar", current_user=current_user, nonce=start_oauth(response, service.settings))}


@router.post("/me/outlook-calendar/sync", summary="Update your Outlook calendar now")
def outlook_calendar_sync(
    db: Session = Depends(get_db), service: IntegrationService = Depends(integration_service), current_user: User = Depends(get_current_user)
) -> dict[str, Any] | None:
    calendar = CalendarSyncService(db, service.settings, http=service.http)
    calendar.sync(current_user.id)
    return calendar.read(current_user)


@router.delete("/me/outlook-calendar", status_code=status.HTTP_204_NO_CONTENT, summary="Stop updating your Outlook calendar")
def outlook_calendar_disconnect(
    db: Session = Depends(get_db), service: IntegrationService = Depends(integration_service), current_user: User = Depends(get_current_user)
) -> Response:
    CalendarSyncService(db, service.settings, http=service.http).disconnect(current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Called by providers -------------------------------------------------------------------------------------


@router.get("/integrations/slack/callback", include_in_schema=False)
def slack_callback(request: Request, service: IntegrationService = Depends(integration_service)) -> RedirectResponse:
    params = request.query_params
    if params.get("error"):
        return back_to_app(service.settings, SETTINGS_PATH, error="Slack wasn't connected.")
    try:
        service.slack_callback(code=params.get("code", ""), state=params.get("state", ""), nonce=request.cookies.get(OAUTH_COOKIE))
    except HTTPException as exc:
        return back_to_app(service.settings, SETTINGS_PATH, error=str(exc.detail))
    except IntegrationNotConfigured:
        return back_to_app(service.settings, SETTINGS_PATH, error="Slack isn't set up for Project Hub yet.")
    return back_to_app(service.settings, f"{SETTINGS_PATH}&connected=slack")


@router.get("/integrations/microsoft/callback", include_in_schema=False)
def microsoft_callback(request: Request, service: IntegrationService = Depends(integration_service)) -> RedirectResponse:
    params = request.query_params
    if params.get("error"):
        return back_to_app(service.settings, SETTINGS_PATH, error=params.get("error_description") or "Microsoft sign-in was cancelled.")
    try:
        path = service.microsoft_callback(code=params.get("code", ""), state=params.get("state", ""), nonce=request.cookies.get(OAUTH_COOKIE))
    except HTTPException as exc:
        return back_to_app(service.settings, SETTINGS_PATH, error=str(exc.detail))
    except IntegrationNotConfigured:
        return back_to_app(service.settings, SETTINGS_PATH, error="Microsoft 365 isn't set up for Project Hub yet.")
    return back_to_app(service.settings, path)


async def verified_slack_body(request: Request, settings: Settings) -> bytes:
    body = await request.body()
    try:
        ok = slack.verify_request(
            settings, timestamp=request.headers.get("X-Slack-Request-Timestamp"), signature=request.headers.get("X-Slack-Signature"), body=body
        )
    except IntegrationNotConfigured:
        ok = False
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature.")
    return body


@router.post("/integrations/slack/commands", include_in_schema=False)
async def slack_commands(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings), http: Http = Depends(get_integrations_http)) -> JSONResponse:
    body = await verified_slack_body(request, settings)
    form = dict(parse_qsl(body.decode()))
    return JSONResponse(handle_slack_command(db, settings, http, form))


@router.post("/integrations/slack/events", include_in_schema=False)
async def slack_events(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings), http: Http = Depends(get_integrations_http)) -> JSONResponse:
    body = await verified_slack_body(request, settings)
    return JSONResponse(handle_slack_event(db, settings, http, json.loads(body or b"{}")))


@router.post("/integrations/teams/messages", include_in_schema=False)
async def teams_messages(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings), http: Http = Depends(get_integrations_http)) -> Response:
    activity = await request.json()
    if not isinstance(activity, dict) or not teams.verify_activity(settings, http, request.headers.get("Authorization"), activity):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    handle_teams_activity(db, settings, http, activity)
    return Response(status_code=status.HTTP_200_OK)


@router.get("/calendar/{token}.ics", include_in_schema=False)
def calendar_feed(token: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Response:
    return Response(
        CalendarFeedService(db, settings).render(token),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "private, max-age=900", "Content-Disposition": 'inline; filename="project-hub.ics"'},
    )


__all__ = ["router", "CHANNEL_EVENTS"]
