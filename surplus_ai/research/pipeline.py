"""Research pipeline: cache → pace → lookup/retry → append ResearchResult → optional 6E apply."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.cache import ResearchResultCache
from surplus_ai.research.candidate import CandidateSelector
from surplus_ai.research.clock import MonotonicFn, SleeperFn, WallClockFn
from surplus_ai.research.enrichment import attempt_workflow_enrichment
from surplus_ai.research.exceptions import CandidateSelectionError, ResearchError
from surplus_ai.research.models import (
    PropertyLookupQuery,
    ResearchCandidate,
    ResearchRunSummary,
)
from surplus_ai.research.persistence import ResearchResultWriter, build_request_payload
from surplus_ai.research.rate_limit import TokenBucketRateLimiter
from surplus_ai.research.registry import ProviderRegistry
from surplus_ai.research.retry import run_with_retry
from surplus_ai.research.review import ResearchReviewQueue

logger = structlog.get_logger(__name__)

__all__ = ["ResearchPipeline", "ResearchError", "load_case_with_relations"]


class ResearchPipeline:
    """Property research. Persists ResearchResult rows and may enqueue review items.

    After a *new* persist, when ``requires_human_review`` is false, invokes Phase 6E-A
    ``apply_research_result`` (fill-missing only). Cache hits do not re-apply.
    Does not create Property/Contact/Lead, change SurplusCase.status, or Compliance.
    Does not change pending-candidate selection.
    """

    def __init__(
        self,
        session: Session,
        registry: ProviderRegistry | None = None,
        *,
        use_cache: bool = True,
        monotonic: MonotonicFn | None = None,
        sleeper: SleeperFn | None = None,
        wall_clock: WallClockFn | None = None,
    ) -> None:
        self._session = session
        self._registry = registry or ProviderRegistry()
        self._candidates = CandidateSelector(session)
        self._writer = ResearchResultWriter(session)
        self._cache = ResearchResultCache(session, wall_clock=wall_clock)
        self._reviews = ResearchReviewQueue(session, registry=self._registry)
        self._use_cache_default = use_cache
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._limiters: dict[tuple[str, float], TokenBucketRateLimiter] = {}

    def research_case(
        self,
        case_id: uuid.UUID,
        *,
        use_cache: bool | None = None,
    ) -> ResearchResult:
        candidate = self._candidates.get_case_candidate(case_id)
        return self._run_one(candidate, use_cache=self._cache_enabled(use_cache))

    def research_pending(
        self,
        *,
        state: str | None = None,
        county_slug: str | None = None,
        limit: int = 100,
        use_cache: bool | None = None,
    ) -> tuple[ResearchRunSummary, list[ResearchResult]]:
        candidates = self._candidates.pending(
            state=state, county_slug=county_slug, limit=limit
        )
        enabled = self._cache_enabled(use_cache)
        results: list[ResearchResult] = []
        success = not_found = error = 0
        for candidate in candidates:
            row = self._run_one(candidate, use_cache=enabled)
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
            .order_by(ResearchResult.fetched_at.desc(), ResearchResult.id.desc())
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
            .order_by(ResearchResult.fetched_at.desc(), ResearchResult.id.desc())
            .limit(limit)
        )
        if case_id is not None:
            stmt = stmt.where(ResearchResult.surplus_case_id == case_id)
        if state:
            stmt = stmt.where(County.state == state.strip().upper())
        if county_slug:
            stmt = stmt.where(County.slug == county_slug.strip().lower())
        return list(self._session.scalars(stmt).all())

    def _cache_enabled(self, use_cache: bool | None) -> bool:
        return self._use_cache_default if use_cache is None else use_cache

    def _limiter(self, provider_name: str, per_second: float) -> TokenBucketRateLimiter:
        key = (provider_name, per_second)
        limiter = self._limiters.get(key)
        if limiter is None:
            limiter = TokenBucketRateLimiter(
                per_second,
                monotonic=self._monotonic,
                sleeper=self._sleeper,
            )
            self._limiters[key] = limiter
        return limiter

    def _run_one(
        self,
        candidate: ResearchCandidate,
        *,
        use_cache: bool,
    ) -> ResearchResult:
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
        policy = self._registry.reliability_for(provider.name)
        request = build_request_payload(provider_name=provider.name, query=query)

        if use_cache:
            hit = self._cache.get(
                surplus_case_id=candidate.surplus_case_id,
                provider=provider.name,
                cache_key=request.cache_key,
                ttl_seconds=policy.cache.ttl_seconds,
            )
            if hit is not None:
                logger.info(
                    "research_cache_hit",
                    case_id=str(candidate.surplus_case_id),
                    provider=provider.name,
                    research_result_id=str(hit.id),
                )
                # Cache hit: existing row only. No new persist, no review enqueue,
                # no enrichment attempt (6E-A remains available for explicit apply).
                return hit

        limiter = self._limiter(provider.name, policy.rate_limit.per_second)
        logger.info(
            "research_lookup_start",
            case_id=str(candidate.surplus_case_id),
            provider=provider.name,
            selection_reason=candidate.selection_reason,
        )
        outcome = run_with_retry(
            lambda: provider.lookup(query),
            policy.retry,
            before_attempt=limiter.acquire,
            sleeper=self._sleeper,
        )
        row = self._writer.persist(
            surplus_case_id=candidate.surplus_case_id,
            provider_name=provider.name,
            query=query,
            outcome=outcome,
        )
        self._reviews.enqueue_for_result(row)
        payload = row.response_payload if isinstance(row.response_payload, dict) else {}
        if payload.get("requires_human_review") is not True:
            # Non-review path: 6E-A decides SUCCESS/found/evidence/fill-missing.
            attempt_workflow_enrichment(
                self._session,
                case_id=row.surplus_case_id,
                research_result_id=row.id,
                trigger="new_result",
            )
        return row


def load_case_with_relations(session: Session, case_id: uuid.UUID) -> SurplusCase:
    case = session.scalar(
        select(SurplusCase)
        .where(SurplusCase.id == case_id)
        .options(selectinload(SurplusCase.county), selectinload(SurplusCase.owners))
    )
    if case is None:
        raise CandidateSelectionError(f"No surplus case with id {case_id}")
    return case
