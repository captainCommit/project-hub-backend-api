from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.shared_access import SharedAccess
from app.models.support_ticket import SupportTicket
from app.models.template import Template
from app.models.user import User
from app.schemas.collaboration import (
    SharedAccessCreate,
    SharedAccessRead,
    SharedAccessUpdate,
    SupportTicketCreate,
    SupportTicketMessageCreate,
    SupportTicketRead,
    SupportTicketUpdate,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
)
from app.services.auth import get_current_user
from app.services.collaboration import SharedAccessService, SupportTicketService, TemplateService


router = APIRouter(prefix="/api/v1", tags=["collaboration"])


@router.get("/shared-access", response_model=list[SharedAccessRead])
def list_shared_access(
    item_type: str = Query(description="PORTFOLIO, PROGRAM or PROJECT"),
    item_id: UUID = Query(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SharedAccess]:
    return SharedAccessService(db).list_shares(item_type=item_type, item_id=item_id, current_user=current_user)


@router.post("/shared-access", response_model=SharedAccessRead, status_code=status.HTTP_201_CREATED)
def create_shared_access(
    share_in: SharedAccessCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SharedAccess:
    return SharedAccessService(db).create_share(share_in=share_in, current_user=current_user)


@router.patch("/shared-access/{share_id}", response_model=SharedAccessRead)
def update_shared_access(
    share_id: UUID,
    share_in: SharedAccessUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SharedAccess:
    return SharedAccessService(db).update_share(share_id=share_id, share_in=share_in, current_user=current_user)


@router.delete("/shared-access/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shared_access(
    share_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    SharedAccessService(db).delete_share(share_id=share_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/accounts/{account_id}/templates", response_model=list[TemplateRead])
def list_templates(
    account_id: UUID,
    template_type: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Template]:
    return TemplateService(db).list_templates(
        account_id=account_id,
        template_type=template_type,
        current_user=current_user,
    )


@router.post("/accounts/{account_id}/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(
    account_id: UUID,
    template_in: TemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Template:
    return TemplateService(db).create_template(
        account_id=account_id,
        template_in=template_in,
        current_user=current_user,
    )


@router.get("/templates/{template_id}", response_model=TemplateRead)
def get_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Template:
    return TemplateService(db).get_template(template_id=template_id, current_user=current_user)


@router.patch("/templates/{template_id}", response_model=TemplateRead)
def update_template(
    template_id: UUID,
    template_in: TemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Template:
    return TemplateService(db).update_template(
        template_id=template_id,
        template_in=template_in,
        current_user=current_user,
    )


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    TemplateService(db).delete_template(template_id=template_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/accounts/{account_id}/support-tickets", response_model=list[SupportTicketRead])
def list_support_tickets(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SupportTicket]:
    return SupportTicketService(db).list_tickets(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/support-tickets",
    response_model=SupportTicketRead,
    status_code=status.HTTP_201_CREATED,
)
def create_support_ticket(
    account_id: UUID,
    ticket_in: SupportTicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SupportTicket:
    return SupportTicketService(db).create_ticket(
        account_id=account_id,
        ticket_in=ticket_in,
        current_user=current_user,
    )


@router.get("/support-tickets/{ticket_id}", response_model=SupportTicketRead)
def get_support_ticket(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SupportTicket:
    return SupportTicketService(db).get_ticket(ticket_id=ticket_id, current_user=current_user)


@router.patch("/support-tickets/{ticket_id}", response_model=SupportTicketRead)
def update_support_ticket(
    ticket_id: UUID,
    ticket_in: SupportTicketUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SupportTicket:
    return SupportTicketService(db).update_ticket(
        ticket_id=ticket_id,
        ticket_in=ticket_in,
        current_user=current_user,
    )


@router.post("/support-tickets/{ticket_id}/messages", response_model=SupportTicketRead)
def add_support_ticket_message(
    ticket_id: UUID,
    message_in: SupportTicketMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SupportTicket:
    return SupportTicketService(db).add_message(
        ticket_id=ticket_id,
        message_in=message_in,
        current_user=current_user,
    )
