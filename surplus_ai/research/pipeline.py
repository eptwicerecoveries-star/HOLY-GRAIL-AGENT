"""Research pipeline skeleton: resolve provider → lookup → append ResearchResult."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.candidate import CandidateSelector
from surplus_ai.research.exceptions import CandidateSelectionError, ResearchError
from surplus_ai.research.models import (
    PropertyLookupQuery,
    ResearchCandidate,
    ResearchRunSummary,
)
from surplus_ai.research.persistence import ResearchResultWriter
from surplus_ai.research.registry import ProviderRegistry

logger = structlog.get_logger(__name__)

__all__ = ["ResearchPipeline", "ResearchError", "load_case_with_relations"]


class ResearchPipeline:
    """Phase 6A property research. Persists ResearchResult rows only.

    Does not update Property, SurplusCase.status, ComplianceEvaluation, Contact, or Lead.
    """

    def __init__(
        self,
        session: Session,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._session = session
        self._registry = registry or ProviderRegistry()
        self._candidates = CandidateSelector(session)
        self._writer = ResearchResultWriter(session)

    def research_case(self, case_id: uuid.UUID) -> ResearchResult:
        candidate = self._candidates.get_case_candidate(case_id)
        return self._run_one(candidate)

    def research_pending(
        self,
        *,
        state: str | None = None,
        county_slug: str | None = None,
        limit: int = 100,
    ) -> tuple[ResearchRunSummary, list[ResearchResult]]:
        candidates = self._candidates.pending(
            state=state, county_slug=county_slug, limit=limit
        )
        results: list[ResearchResult] = []
        success = not_found = error = 0
        for candidate in candidates:
            row = self._run_one(candidate)
            results.append(row)
            if row.status.value == "success":
                success += 1
            elif row.status.value == "not_found":
                not_found += 1
            else:
                error += 1
        summary = ResearchRunSummary(
            cases_attempted=len(candidates),
            results_written=len(results),
            success=success,
            not_found=not_found,
            error=error,
        )
        return summary, results

    def latest_for_case(self, case_id: uuid.UUID) -> ResearchResult | None:
        return self._session.scalar(
            select(ResearchResult)
            .where(ResearchResult.surplus_case_id == case_id)
            .order_by(ResearchResult.fetched_at.desc())
            .limit(1)
        )

    def status_rows(
        self,
        *,
        state: str | None = None,
        county_slug: str | None = None,
        case_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[ResearchResult]:
        from surplus_ai.database.models.county import County

        stmt = (
            select(ResearchResult)
            .join(SurplusCase, ResearchResult.surplus_case_id == SurplusCase.id)
            .join(County, SurplusCase.county_id == County.id)
            .order_by(ResearchResult.fetched_at.desc())
            .limit(limit)
        )
        if case_id is not None:
            stmt = stmt.where(ResearchResult.surplus_case_id == case_id)
        if state:
            stmt = stmt.where(County.state == state.strip().upper())
        if county_slug:
            stmt = stmt.where(County.slug == county_slug.strip().lower())
        return list(self._session.scalars(stmt).all())

    def _run_one(self, candidate: ResearchCandidate) -> ResearchResult:
        query = PropertyLookupQuery(
            state=candidate.state,
            county_slug=candidate.county_slug,
            parcel_id=candidate.parcel_id,
            owner_raw_name=candidate.owner_raw_name,
            property_address_raw=candidate.property_address_raw,
            sale_date=candidate.sale_date,
            owner_type=candidate.owner_type,
            surplus_case_id=candidate.surplus_case_id,
        )
        provider = self._registry.resolve_for_county(candidate.state, candidate.county_slug)
        logger.info(
            "research_lookup_start",
            case_id=str(candidate.surplus_case_id),
            provider=provider.name,
            selection_reason=candidate.selection_reason,
        )
        outcome = provider.lookup(query)
        return self._writer.persist(
            surplus_case_id=candidate.surplus_case_id,
            provider_name=provider.name,
            query=query,
            outcome=outcome,
        )


def load_case_with_relations(session: Session, case_id: uuid.UUID) -> SurplusCase:
    case = session.scalar(
        select(SurplusCase)
        .where(SurplusCase.id == case_id)
        .options(selectinload(SurplusCase.county), selectinload(SurplusCase.owners))
    )
    if case is None:
        raise CandidateSelectionError(f"No surplus case with id {case_id}")
    return case
