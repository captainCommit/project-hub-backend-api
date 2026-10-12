from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.services.email_delivery import SETTINGS_PATH, app_base, read_unsubscribe_token
from app.services.notification_preferences import CATEGORY_LABELS, NotificationPreferenceService


# Opened from email links, so no sign-in: the signed token says who and what.
router = APIRouter(prefix="/api/v1/email", tags=["email"])


def _page(settings: Settings, heading: str, body: str, *, status_code: int = 200) -> HTMLResponse:
    settings_url = f"{app_base(settings)}{SETTINGS_PATH}"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Project Hub email settings</title></head>
<body style="margin:0;background:#f1f5f9;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1e293b">
<main style="max-width:480px;margin:48px auto;padding:0 16px">
<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:28px">
<p style="margin:0 0 12px;font-size:13px;font-weight:600;color:#002046">Project Hub</p>
<h1 style="margin:0 0 8px;font-size:20px;color:#002046">{escape(heading)}</h1>
{body}
<p style="margin:20px 0 0;font-size:13px"><a href="{escape(settings_url)}" style="color:#c87b14">Manage all email settings</a></p>
</div></main></body></html>"""
    return HTMLResponse(html, status_code=status_code)


def _what(category: str) -> str:
    return "all Project Hub emails" if category == "all" else f"emails about {CATEGORY_LABELS[category]}"


def _invalid(settings: Settings) -> HTMLResponse:
    return _page(
        settings,
        "This link doesn't work",
        '<p style="margin:0;font-size:14px;line-height:22px">It may have been cut off by your email app. '
        "You can change your email settings in Project Hub instead.</p>",
        status_code=400,
    )


@router.get("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
def confirm_unsubscribe(
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    # Mail scanners open links, so a GET only asks; the button POSTs.
    parsed = read_unsubscribe_token(settings.email_token_secret, token)
    user = db.get(User, parsed[0]) if parsed else None
    if parsed is None or user is None:
        return _invalid(settings)
    return _page(
        settings,
        "Unsubscribe?",
        f'<p style="margin:0 0 20px;font-size:14px;line-height:22px">Stop sending {escape(_what(parsed[1]))} '
        f"to <strong>{escape(user.email)}</strong>? In-app notifications are not affected.</p>"
        '<form method="post"><button type="submit" style="background:#c87b14;color:#fff;border:0;border-radius:8px;'
        'padding:10px 18px;font-size:14px;font-weight:600;cursor:pointer">Unsubscribe</button></form>',
    )


@router.post("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
def unsubscribe(
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """The confirm button, and one-click unsubscribe from mail apps (RFC 8058 List-Unsubscribe-Post)."""
    parsed = read_unsubscribe_token(settings.email_token_secret, token)
    if parsed is None or db.get(User, parsed[0]) is None:
        return _invalid(settings)
    user_id, category = parsed
    NotificationPreferenceService(db).unsubscribe(user_id, category)
    return _page(
        settings,
        "You're unsubscribed",
        f'<p style="margin:0;font-size:14px;line-height:22px">You won\'t get {escape(_what(category))} any more. '
        "You can turn them back on in your email settings.</p>",
    )
