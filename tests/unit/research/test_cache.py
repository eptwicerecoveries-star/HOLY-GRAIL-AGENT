from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    ResearchStatus,
    SurplusCaseStatus,
    SurplusSourceType,
)
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.cache import ResearchResultCache
from surplus_ai.research.models import PropertyLookupQuery
from surplus_ai.research.persistence import (
    ResearchResultWriter,
    build_request_payload,
    parse_outcome_dict,
)
from tests.unit.research.conftest import FakeClock


def _cache_key(provider: str, query: PropertyLookupQuery) -> str:
    return build_request_payload(provider_name=provider, query=query).cache_key


def _insert_row(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    *,
    provider: str = "manual_lookup",
    status: ResearchStatus = ResearchStatus.NOT_FOUND,
    provider_status: str = "not_found",
    cacheable: bool | None = True,
    found: bool = False,
    row_id: uuid.UUID | None = None,
    fetched_at: datetime | None = None,
) -> ResearchResult:
    request = build_request_payload(provider_name=provider, query=sample_query)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "found": found,
        "requires_human_review": True,
        "provider_status": provider_status,
        "evidence": [],
    }
    if cacheable is not None:
        payload["cacheable"] = cacheable
    row = ResearchResult(
        surplus_case_id=sample_case.id,
        provider=provider,
        request_payload=request.model_dump(mode="json"),
        response_payload=payload,
        status=status,
    )
    if row_id is not None:
        row.id = row_id
    session.add(row)
    session.flush()
    if fetched_at is not None:
        row.fetched_at = fetched_at
        session.flush()
    return row


def _lookup(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    *,
    provider: str = "manual_lookup",
    ttl_seconds: int = 86400,
    wall_clock: Any = None,
) -> ResearchResult | None:
    cache = ResearchResultCache(session, wall_clock=wall_clock)
    return cache.get(
        surplus_case_id=sample_case.id,
        provider=provider,
        cache_key=_cache_key(provider, sample_query),
        ttl_seconds=ttl_seconds,
    )


def test_legacy_row_missing_cacheable_is_miss(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(session, sample_case, sample_query, cacheable=None)
    assert _lookup(session, sample_case, sample_query) is None


def test_explicit_cacheable_true_success_is_hit(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    row = _insert_row(
        session,
        sample_case,
        sample_query,
        status=ResearchStatus.SUCCESS,
        provider_status="success",
        found=True,
        cacheable=True,
    )
    hit = _lookup(session, sample_case, sample_query)
    assert hit is not None
    assert hit.id == row.id


def test_explicit_cacheable_true_not_found_is_hit(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    row = _insert_row(session, sample_case, sample_query, cacheable=True)
    hit = _lookup(session, sample_case, sample_query)
    assert hit is not None
    assert hit.id == row.id


def test_cacheable_false_is_miss(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(session, sample_case, sample_query, cacheable=False)
    assert _lookup(session, sample_case, sample_query) is None


def test_expired_row_is_miss(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(
        session,
        sample_case,
        sample_query,
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    clock = FakeClock()
    # FakeClock.wall starts at 2026-01-01 + 1000s; still within 86400s.
    # Jump monotonic/sleep so wall is past TTL.
    clock.sleep(90000)
    assert (
        _lookup(session, sample_case, sample_query, wall_clock=clock.wall) is None
    )


def test_error_never_cache_hit(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(
        session,
        sample_case,
        sample_query,
        status=ResearchStatus.ERROR,
        provider_status="error",
        cacheable=True,
    )
    assert _lookup(session, sample_case, sample_query) is None


def test_timeout_never_cache_hit(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(
        session,
        sample_case,
        sample_query,
        status=ResearchStatus.ERROR,
        provider_status="timeout",
        cacheable=True,
    )
    assert _lookup(session, sample_case, sample_query) is None


def test_rate_limited_never_cache_hit(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(
        session,
        sample_case,
        sample_query,
        status=ResearchStatus.ERROR,
        provider_status="rate_limited",
        cacheable=True,
    )
    assert _lookup(session, sample_case, sample_query) is None


def test_skipped_never_cache_hit(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    # Persistence maps SKIPPED → NOT_FOUND; provider_status must still block a hit.
    _insert_row(
        session,
        sample_case,
        sample_query,
        status=ResearchStatus.NOT_FOUND,
        provider_status="skipped",
        cacheable=True,
    )
    assert _lookup(session, sample_case, sample_query) is None


def test_deterministic_newest_row_selection(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    clock = FakeClock()
    now = clock.wall()
    older = _insert_row(
        session,
        sample_case,
        sample_query,
        row_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        fetched_at=now - timedelta(hours=2),
    )
    newer = _insert_row(
        session,
        sample_case,
        sample_query,
        row_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        fetched_at=now - timedelta(hours=1),
    )
    hit = _lookup(session, sample_case, sample_query, wall_clock=clock.wall)
    assert hit is not None
    assert hit.id == newer.id
    assert hit.id != older.id

    same_ts = now - timedelta(minutes=10)
    same_ts_low = _insert_row(
        session,
        sample_case,
        sample_query,
        provider="fixture_open_data",
        row_id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
        fetched_at=same_ts,
    )
    same_ts_high = _insert_row(
        session,
        sample_case,
        sample_query,
        provider="fixture_open_data",
        row_id=uuid.UUID("00000000-0000-0000-0000-000000000020"),
        fetched_at=same_ts,
    )
    hit_id = _lookup(
        session,
        sample_case,
        sample_query,
        provider="fixture_open_data",
        wall_clock=clock.wall,
    )
    assert hit_id is not None
    assert hit_id.id == same_ts_high.id
    assert hit_id.id != same_ts_low.id


def test_wrong_provider_is_miss(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(session, sample_case, sample_query, provider="manual_lookup")
    assert (
        _lookup(session, sample_case, sample_query, provider="fixture_open_data")
        is None
    )


def test_wrong_cache_key_is_miss(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(session, sample_case, sample_query)
    cache = ResearchResultCache(session)
    assert (
        cache.get(
            surplus_case_id=sample_case.id,
            provider="manual_lookup",
            cache_key="not-the-real-key",
            ttl_seconds=86400,
        )
        is None
    )


def test_wrong_case_is_miss(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    sample_county: County,
) -> None:
    other = SurplusCase(
        county_id=sample_county.id,
        parcel_id="99-999999",
        property_address_raw="999 OTHER ST",
        sale_date=date(2024, 3, 1),
        surplus_amount=Decimal("1.00"),
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash="research-test-hash-other-case",
    )
    session.add(other)
    session.flush()
    _insert_row(session, sample_case, sample_query)
    cache = ResearchResultCache(session)
    assert (
        cache.get(
            surplus_case_id=other.id,
            provider="manual_lookup",
            cache_key=_cache_key("manual_lookup", sample_query),
            ttl_seconds=86400,
        )
        is None
    )


def test_ttl_zero_is_always_miss(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    _insert_row(session, sample_case, sample_query)
    assert _lookup(session, sample_case, sample_query, ttl_seconds=0) is None


def test_cache_hit_does_not_update_or_append(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
) -> None:
    row = _insert_row(session, sample_case, sample_query)
    original_fetched = row.fetched_at
    hit = _lookup(session, sample_case, sample_query)
    assert hit is not None
    assert hit.id == row.id
    session.refresh(row)
    assert row.fetched_at == original_fetched
    count = session.scalar(select(func.count()).select_from(ResearchResult))
    assert count == 1


def test_persisted_success_is_explicitly_cacheable(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    row = ResearchResultWriter(session).persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture_open_data",
        query=sample_query,
        outcome=parse_outcome_dict(outcome_fixtures["success"]),
    )
    assert row.response_payload["cacheable"] is True
    hit = ResearchResultCache(session).get(
        surplus_case_id=sample_case.id,
        provider="fixture_open_data",
        cache_key=row.request_payload["cache_key"],
        ttl_seconds=86400,
    )
    assert hit is not None
    assert hit.id == row.id


def test_persisted_error_is_not_cacheable(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    row = ResearchResultWriter(session).persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture",
        query=sample_query,
        outcome=parse_outcome_dict(outcome_fixtures["error"]),
    )
    assert row.response_payload["cacheable"] is False
    assert (
        ResearchResultCache(session).get(
            surplus_case_id=sample_case.id,
            provider="fixture",
            cache_key=row.request_payload["cache_key"],
            ttl_seconds=86400,
        )
        is None
    )
