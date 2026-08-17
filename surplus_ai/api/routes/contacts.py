"""Read-only Contact metadata endpoints — Contact.value is never exposed."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from surplus_ai.api.dependencies import get_db_session
from surplus_ai.api.errors import not_found
from surplus_ai.api.pagination import pagination_params
from surplus_ai.api.queries import get_contact, list_contacts
from surplus_ai.api.schemas import ContactResponse
from surplus_ai.database.models.contact import Contact

router = APIRouter(tags=["contacts"])


def _to_contact(contact: Contact) -> ContactResponse:
    return ContactResponse(
        id=contact.id,
        lead_id=contact.lead_id,
        contact_type=contact.contact_type.value,
        source=contact.source,
        confidence=contact.confidence,
        is_verified=contact.is_verified,
        created_at=contact.created_at,
    )


@router.get("/contacts", response_model=list[ContactResponse])
def get_contacts(
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> list[ContactResponse]:
    limit, offset = pagination
    return [_to_contact(c) for c in list_contacts(session, limit=limit, offset=offset)]


@router.get("/contacts/{contact_id}", response_model=ContactResponse)
def get_contact_detail(
    contact_id: UUID,
    session: Session = Depends(get_db_session),
) -> ContactResponse:
    contact = get_contact(session, contact_id)
    if contact is None:
        raise not_found("Contact")
    return _to_contact(contact)
