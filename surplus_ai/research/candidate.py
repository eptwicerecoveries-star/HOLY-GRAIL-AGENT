"""Select surplus cases that are eligible for property research."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from surplus_ai.database.models.county import County
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.exceptions import CandidateSelectionError
from surplus_ai.research.models import ResearchCandidate

logger = structlog.get_logger(__name__)


class CandidateSelector:
    """Choose cases that have enough published identity to research.

    Does not create leads or contacts. Does not consult compliance eligibility —
    research collects evidence; compliance remains authoritative for contact.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_case_candidate(self, case_id: uuid.UUID) -> ResearchCandidate:
        case = self._session.scalar(
            select(SurplusCase)
            .where(SurplusCase.id == case_id)
            .options(selectinload(SurplusCase.county), selectinload(SurplusCase.owners))
        )
        if case is None:
            raise CandidateSelectionError(f"No surplus case with id {case_id}")
        candidate = self._to_candidate(case)
        if candidate is None:
            raise CandidateSelectionError(
                f"Case {case_id} lacks researchable identity "
                "(need parcel_id, or address plus owner name, or case_number)."
            )
        return candidate

    def pending(
        self,
        *,
        state: str | None = None,
        county_slug: str | None = None,
        limit: int = 100,
    ) -> list[ResearchCandidate]:
        if limit < 1:
            raise CandidateSelectionError("limit must be >= 1")

        stmt = (
            select(SurplusCase)
            .join(County, SurplusCase.county_id == County.id)
            .options(selectinload(SurplusCase.county), selectinload(SurplusCase.owners))
            .order_by(SurplusCase.created_at.asc())
        )
        if state:
            stmt = stmt.where(County.state == state.strip().upper())
        if county_slug:
            stmt = stmt.where(County.slug == county_slug.strip().lower())
        stmt = stmt.limit(limit)

        cases = list(self._session.scalars(stmt).unique().all())
        selected: list[ResearchCandidate] = []
        for case in cases:
            candidate = self._to_candidate(case)
            if candidate is not None:
                selected.append(candidate)

        logger.info(
            "research_candidates_selected",
            considered=len(cases),
            selected=len(selected),
            state=state,
            county_slug=county_slug,
        )
        return selected

    def _to_candidate(self, case: SurplusCase) -> ResearchCandidate | None:
        county = case.county
        owner = case.owners[0] if case.owners else None
        owner_name = owner.raw_name if owner else None
        owner_type = owner.owner_type.value if owner else None

        reason = _selection_reason(
            parcel_id=case.parcel_id,
            address=case.property_address_raw,
            owner_name=owner_name,
            case_number=case.case_number,
        )
        if reason is None:
            return None

        return ResearchCandidate(
            surplus_case_id=case.id,
            county_id=case.county_id,
            state=county.state,
            county_slug=county.slug,
            parcel_id=case.parcel_id,
            property_address_raw=case.property_address_raw,
            owner_raw_name=owner_name,
            owner_type=owner_type,
            sale_date=case.sale_date.isoformat() if case.sale_date else None,
            selection_reason=reason,
        )


def _selection_reason(
    *,
    parcel_id: str | None,
    address: str | None,
    owner_name: str | None,
    case_number: str | None,
) -> str | None:
    if parcel_id and parcel_id.strip():
        return "parcel_id"
    if address and address.strip() and owner_name and owner_name.strip():
        return "address_and_owner"
    if case_number and case_number.strip():
        return "case_number"
    return None
