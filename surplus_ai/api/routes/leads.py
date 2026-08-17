"""Read-only Lead endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from surplus_ai.api.dependencies import get_db_session, require_active_user
from surplus_ai.api.errors import not_found
from surplus_ai.api.pagination import pagination_params
from surplus_ai.api.queries import get_lead, list_leads
from surplus_ai.api.schemas import LeadResponse
from surplus_ai.database.models.lead import Lead

router = APIRouter(tags=["leads"], dependencies=[Depends(require_active_user)])


def _to_lead(lead: Lead, contact_count: int) -> LeadResponse:
    return LeadResponse(
        id=lead.id,
        surplus_case_id=lead.surplus_case_id,
        status=lead.status.value,
        score=lead.score,
        assigned_user_id=lead.assigned_user_id,
        contact_count=contact_count,
        qualified_at=lead.qualified_at,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


@router.get("/leads", response_model=list[LeadResponse])
def get_leads(
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> list[LeadResponse]:
    limit, offset = pagination
    rows = list_leads(session, limit=limit, offset=offset)
    return [_to_lead(lead, count) for lead, count in rows]


@router.get("/leads/{lead_id}", response_model=LeadResponse)
def get_lead_detail(
    lead_id: UUID,
    session: Session = Depends(get_db_session),
) -> LeadResponse:
    row = get_lead(session, lead_id)
    if row is None:
        raise not_found("Lead")
    lead, count = row
    return _to_lead(lead, count)
