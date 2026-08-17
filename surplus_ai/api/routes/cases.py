"""Read-only SurplusCase endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from surplus_ai.api.dependencies import get_db_session
from surplus_ai.api.errors import not_found
from surplus_ai.api.pagination import pagination_params
from surplus_ai.api.queries import get_case, list_cases
from surplus_ai.api.schemas import CaseResponse
from surplus_ai.database.models.county import County
from surplus_ai.database.models.surplus_case import SurplusCase

router = APIRouter(tags=["cases"])


def _to_case(case: SurplusCase, county: County, has_lead: bool) -> CaseResponse:
    return CaseResponse(
        id=case.id,
        county_id=case.county_id,
        county_state=county.state,
        county_slug=county.slug,
        county_name=county.name,
        parcel_id=case.parcel_id,
        case_number=case.case_number,
        surplus_amount=case.surplus_amount,
        surplus_is_explicit=case.surplus_is_explicit,
        status=case.status.value,
        sale_date=case.sale_date,
        has_lead=has_lead,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


@router.get("/cases", response_model=list[CaseResponse])
def get_cases(
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> list[CaseResponse]:
    limit, offset = pagination
    rows = list_cases(session, limit=limit, offset=offset)
    return [_to_case(case, county, has_lead) for case, county, has_lead in rows]


@router.get("/cases/{case_id}", response_model=CaseResponse)
def get_case_detail(
    case_id: UUID,
    session: Session = Depends(get_db_session),
) -> CaseResponse:
    row = get_case(session, case_id)
    if row is None:
        raise not_found("Case")
    case, county, has_lead = row
    return _to_case(case, county, has_lead)
