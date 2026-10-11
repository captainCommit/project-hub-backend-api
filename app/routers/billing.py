from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.billing import AccountBillingRead, RedirectRead
from app.services.auth import get_current_user
from app.services.billing import BillingService


router = APIRouter(prefix="/api/v1", tags=["billing"])


@router.get("/accounts/{account_id}/billing", response_model=AccountBillingRead, summary="Seats, subscription and invoices (owners)")
def get_account_billing(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    return BillingService(db, settings=settings).overview(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/billing/checkout",
    response_model=RedirectRead,
    summary="Start a Stripe Checkout to add a card and subscribe (owners)",
)
def start_billing_checkout(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RedirectRead:
    url = BillingService(db, settings=settings).start_checkout(account_id=account_id, current_user=current_user)
    return RedirectRead(url=url)


@router.post(
    "/accounts/{account_id}/billing/portal",
    response_model=RedirectRead,
    summary="Open Stripe's billing portal: cards, invoices, cancelling (owners)",
)
def open_billing_portal(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RedirectRead:
    url = BillingService(db, settings=settings).open_portal(account_id=account_id, current_user=current_user)
    return RedirectRead(url=url)


@router.post("/billing/stripe-webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    # Signed by Stripe rather than by a signed-in user; the signature check is the authentication.
    payload = await request.body()
    result = BillingService(db, settings=settings).handle_webhook(payload=payload, signature=stripe_signature)
    return {"status": result}
