"""Read-only ResearchResult cache. Hits never mutate or append rows."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models.enums import ResearchStatus
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.research.clock import WallClockFn, real_wall_clock
from surplus_ai.research.models import ProviderOutcomeStatus

_CACHEABLE_STATUSES = frozenset({ResearchStatus.SUCCESS, ResearchStatus.NOT_FOUND})
_CACHEABLE_PROVIDER_STATUSES = frozenset(
    {ProviderOutcomeStatus.SUCCESS.value, ProviderOutcomeStatus.NOT_FOUND.value}
)


def response_is_explicitly_cacheable(payload: dict[str, object] | None) -> bool:
    """Fail-closed: only JSON true counts. Missing/None/false are misses."""
    if not payload:
        return False
    return payload.get("cacheable") is True


class ResearchResultCache:
    """Lookup prior append-only rows. Never updates ``fetched_at`` or payloads."""

    def __init__(
        self,
        session: Session,
        *,
        wall_clock: WallClockFn | None = None,
    ) -> None:
        self._session = session
        self._wall_clock: Callable[[], datetime] = wall_clock or real_wall_clock

    def get(
        self,
        *,
        surplus_case_id: uuid.UUID,
        provider: str,
        cache_key: str,
        ttl_seconds: int,
    ) -> ResearchResult | None:
        if ttl_seconds <= 0:
            return None
        now = self._wall_clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        cutoff = now - timedelta(seconds=ttl_seconds)

        rows = list(
            self._session.scalars(
                select(ResearchResult)
                .where(ResearchResult.surplus_case_id == surplus_case_id)
                .where(ResearchResult.provider == provider)
                .order_by(ResearchResult.fetched_at.desc(), ResearchResult.id.desc())
            ).all()
        )
        for row in rows:
            request = row.request_payload or {}
            if request.get("cache_key") != cache_key:
                continue
            if not response_is_explicitly_cacheable(row.response_payload):
                continue
            if row.status not in _CACHEABLE_STATUSES:
                continue
            provider_status = (row.response_payload or {}).get("provider_status")
            if provider_status not in _CACHEABLE_PROVIDER_STATUSES:
                continue
            fetched = row.fetched_at
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=UTC)
            if fetched < cutoff:
                continue
            return row
        return None
