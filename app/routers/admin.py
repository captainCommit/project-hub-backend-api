from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params, paginated_response
from app.models.account import Account
from app.models.billing import Invoice
from app.models.user import User
from app.schemas.account import PlatformAccountRead
from app.schemas.billing import BillingState, BillingToggle, InvoiceRead
from app.schemas.collaboration import SupportTicketMessageCreate, TicketStatus
from app.schemas.platform import AdminSupportTicketRead, AdminSupportTicketUpdate, BillingSummary, PlatformAuditEventRead, SystemStatus
from app.services.accounts import AccountService, require_platform_admin
from app.services.auth import get_current_user
from app.services.billing import BillingService
from app.services.platform import PlatformService
from app.services.platform_audit import PlatformAuditAction, list_platform_actions, record_platform_action
from app.services.support_admin import SupportAdminService


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/accounts", response_model=list[PlatformAccountRead], summary="List every account (platform admins)")
def list_all_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PlatformAccountRead]:
    return AccountService(db).list_all_accounts(current_user=current_user)


@router.get("/billing", response_model=BillingSummary, summary="Seats and monthly cost per account (platform admins)")
def get_billing_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> BillingSummary:
    return PlatformService(db, settings=settings).billing_summary(current_user=current_user)


@router.get("/system", response_model=SystemStatus, summary="API, database and usage status (platform admins)")
def get_system_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> SystemStatus:
    return PlatformService(db, settings=settings).system_status(current_user=current_user)


@router.get(
    "/audit",
    response_model=PaginatedResponse[PlatformAuditEventRead],
    summary="What platform admins did, newest first (platform admins)",
)
def list_audit_events(
    account_id: UUID | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    require_platform_admin(current_user)
    events, total = list_platform_actions(db, pagination=pagination, account_id=account_id)
    items = [PlatformAuditEventRead.model_validate(event) for event in events]
    return paginated_response(items=items, total=total, pagination=pagination)


@router.patch(
    "/accounts/{account_id}/billing",
    response_model=BillingState,
    summary="Turn billing on or off for an account (platform admins)",
)
def set_account_billing(
    account_id: UUID,
    toggle: BillingToggle,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    require_platform_admin(current_user)
    service = BillingService(db, settings=settings)
    billing = service.set_billing_enabled(account_id=account_id, enabled=toggle.billing_enabled, actor=current_user)
    return service.state(billing)


@router.post(
    "/accounts/{account_id}/billing/sync",
    summary="Push the account's current seat counts to Stripe (platform admins)",
)
def sync_account_billing(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    require_platform_admin(current_user)
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    service = BillingService(db, settings=settings)
    service.gateway_or_503()
    changed = service.sync_seats(account_id)
    record_platform_action(
        db, actor=current_user, action=PlatformAuditAction.ACCOUNT_BILLING_SYNCED, account=account, details={"changed": changed}
    )
    db.commit()
    return {"changed": changed}


@router.get(
    "/invoices",
    response_model=PaginatedResponse[InvoiceRead],
    summary="Invoices across accounts, newest first (platform admins)",
)
def list_invoices(
    account_id: UUID | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    require_platform_admin(current_user)
    statement = select(Invoice)
    if account_id is not None:
        statement = statement.where(Invoice.account_id == account_id)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    invoices = db.scalars(
        statement.order_by(Invoice.issued_at.desc().nulls_last(), Invoice.id)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return paginated_response(
        items=[InvoiceRead.model_validate(invoice) for invoice in invoices], total=total, pagination=pagination
    )


# Support tickets from every account ----------------------------------------------------------------------


@router.get("/support-tickets", response_model=list[AdminSupportTicketRead], summary="Support tickets across accounts (platform admins)")
def list_support_tickets(
    status_filter: TicketStatus | Literal["ACTIVE"] | None = Query(default="ACTIVE", alias="status"),
    search: str | None = Query(default=None, max_length=200),
    account_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    require_platform_admin(current_user)
    return SupportAdminService(db).list(status_filter=status_filter, search=search, account_id=account_id)


@router.get("/support-tickets/summary", summary="How many tickets are open and waiting for a reply (platform admins)")
def support_ticket_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, int]:
    require_platform_admin(current_user)
    return SupportAdminService(db).counts()


@router.get("/support-tickets/{ticket_id}", response_model=AdminSupportTicketRead, summary="A support ticket and its conversation (platform admins)")
def get_support_ticket(ticket_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, object]:
    require_platform_admin(current_user)
    return SupportAdminService(db).detail(ticket_id)


@router.post("/support-tickets/{ticket_id}/messages", response_model=AdminSupportTicketRead, summary="Reply as Project Hub Support (platform admins)")
def reply_to_support_ticket(
    ticket_id: UUID,
    message_in: SupportTicketMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    require_platform_admin(current_user)
    return SupportAdminService(db).reply(ticket_id=ticket_id, message_in=message_in, actor=current_user)


@router.patch("/support-tickets/{ticket_id}", response_model=AdminSupportTicketRead, summary="Change a ticket's status or priority (platform admins)")
def update_support_ticket(
    ticket_id: UUID,
    changes: AdminSupportTicketUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    require_platform_admin(current_user)
    return SupportAdminService(db).update(ticket_id=ticket_id, changes=changes.model_dump(exclude_unset=True), actor=current_user)
