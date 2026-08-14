from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.enums import ResearchStatus
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.pipeline import ResearchPipeline
from surplus_ai.research.registry import ProviderRegistry
from tests.unit.research.conftest import (
    SHIPPED_RELIABILITY,
    FakeClock,
    FixtureProvider,
    SequenceProvider,
    fixture_outcome,
    force_provider,
    registry_from_yaml,
)


def test_pipeline_manual_default_writes_not_found(
    session: Session, sample_case: SurplusCase
) -> None:
    pipeline = ResearchPipeline(session)
    row = pipeline.research_case(sample_case.id)
    assert row.status is ResearchStatus.NOT_FOUND
    assert row.provider == "manual_lookup"
    assert row.response_payload["requires_human_review"] is True
    assert row.response_payload["found"] is False


def test_pipeline_with_fixture_success_provider(
    session: Session,
    sample_case: SurplusCase,
    outcome_fixtures: dict[str, Any],
) -> None:
    registry = ProviderRegistry()
    registry.register(
        FixtureProvider("fixture_open_data", fixture_outcome(outcome_fixtures, "success"))
    )
    # Force this provider via custom resolve: register and temporarily point default
    # by resolving through register + county override is heavy; instead monkey the
    # registry.resolve_for_county.
    original = registry.resolve_for_county

    def _force(_state: str, _slug: str) -> Any:
        return registry.resolve("fixture_open_data")

    registry.resolve_for_county = _force  # type: ignore[method-assign]
    try:
        row = ResearchPipeline(session, registry=registry).research_case(sample_case.id)
    finally:
        registry.resolve_for_county = original  # type: ignore[method-assign]

    assert row.status is ResearchStatus.SUCCESS
    assert row.response_payload["found"] is True


def test_pipeline_pending_and_side_effect_free(
    session: Session, sample_case: SurplusCase
) -> None:
    before = sample_case.status
    summary, rows = ResearchPipeline(session).research_pending(state="MD", limit=10)
    assert summary.cases_attempted >= 1
    assert summary.results_written == len(rows)
    assert summary.not_found >= 1
    session.refresh(sample_case)
    assert sample_case.status is before
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Property)) == 0
    assert session.scalar(select(func.count()).select_from(ComplianceEvaluation)) == 0


def test_pipeline_append_only_on_repeat(
    session: Session, sample_case: SurplusCase
) -> None:
    pipeline = ResearchPipeline(session)
    first = pipeline.research_case(sample_case.id, use_cache=False)
    first_id = first.id
    first_fetched = first.fetched_at
    first_status = first.status
    first_provider = first.provider
    first_request = dict(first.request_payload or {})
    first_response = dict(first.response_payload or {})
    second = pipeline.research_case(sample_case.id, use_cache=False)
    assert second.id != first_id
    session.refresh(first)
    assert first.id == first_id
    assert first.fetched_at == first_fetched
    assert first.status is first_status
    assert first.provider == first_provider
    assert first.request_payload == first_request
    assert first.response_payload == first_response
    count = session.scalar(
        select(func.count())
        .select_from(ResearchResult)
        .where(ResearchResult.surplus_case_id == sample_case.id)
    )
    assert count == 2


def test_pipeline_error_provider(
    session: Session,
    sample_case: SurplusCase,
    outcome_fixtures: dict[str, Any],
) -> None:
    registry = ProviderRegistry()
    registry.register(
        FixtureProvider("fixture_error", fixture_outcome(outcome_fixtures, "error"))
    )
    registry.resolve_for_county = lambda _s, _c: registry.resolve("fixture_error")  # type: ignore[method-assign]
    row = ResearchPipeline(session, registry=registry).research_case(sample_case.id)
    assert row.status is ResearchStatus.ERROR
    assert row.response_payload["found"] is False


def _forced_pipeline(
    session: Session,
    tmp_path: Any,
    provider: Any,
    *,
    reliability: dict[str, Any] | None,
    clock: FakeClock | None = None,
    use_cache: bool = True,
) -> ResearchPipeline:
    registry = registry_from_yaml(tmp_path, reliability=reliability)
    registry.register(provider)
    force_provider(registry, provider.name)
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs = {
            "monotonic": clock.monotonic,
            "sleeper": clock.sleep,
            "wall_clock": clock.wall,
        }
    return ResearchPipeline(
        session, registry=registry, use_cache=use_cache, **kwargs
    )


def test_no_cache_bypasses_read_but_still_appends(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "success")
    )
    pipeline = _forced_pipeline(
        session, tmp_path, provider, reliability=SHIPPED_RELIABILITY
    )
    first = pipeline.research_case(sample_case.id)
    second = pipeline.research_case(sample_case.id, use_cache=False)
    assert provider.calls == 2
    assert first.id != second.id
    assert second.response_payload["cacheable"] is True
    count = session.scalar(
        select(func.count())
        .select_from(ResearchResult)
        .where(ResearchResult.surplus_case_id == sample_case.id)
    )
    assert count == 2


def test_cache_hit_returns_same_row_no_append(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "success")
    )
    pipeline = _forced_pipeline(
        session, tmp_path, provider, reliability=SHIPPED_RELIABILITY
    )
    first = pipeline.research_case(sample_case.id)
    fetched = first.fetched_at
    second = pipeline.research_case(sample_case.id)
    assert second.id == first.id
    assert provider.calls == 1
    session.refresh(first)
    assert first.fetched_at == fetched
    count = session.scalar(
        select(func.count())
        .select_from(ResearchResult)
        .where(ResearchResult.surplus_case_id == sample_case.id)
    )
    assert count == 1


def test_timeout_retries_then_persists_final(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = SequenceProvider(
        "fixture_timeout",
        [
            fixture_outcome(outcome_fixtures, "timeout"),
            fixture_outcome(outcome_fixtures, "timeout"),
            fixture_outcome(outcome_fixtures, "not_found"),
        ],
    )
    clock = FakeClock()
    pipeline = _forced_pipeline(
        session,
        tmp_path,
        provider,
        reliability=SHIPPED_RELIABILITY,
        clock=clock,
        use_cache=False,
    )
    row = pipeline.research_case(sample_case.id)
    assert provider.calls == 3
    assert row.status is ResearchStatus.NOT_FOUND
    assert clock.sleeps == [0.5, 0.5]


def test_provider_rate_limited_retries(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = SequenceProvider(
        "fixture_rl",
        [
            fixture_outcome(outcome_fixtures, "rate_limited"),
            fixture_outcome(outcome_fixtures, "rate_limited"),
            fixture_outcome(outcome_fixtures, "rate_limited"),
        ],
    )
    clock = FakeClock()
    pipeline = _forced_pipeline(
        session,
        tmp_path,
        provider,
        reliability=SHIPPED_RELIABILITY,
        clock=clock,
        use_cache=False,
    )
    row = pipeline.research_case(sample_case.id)
    assert provider.calls == 3
    assert row.status is ResearchStatus.ERROR
    assert row.response_payload["provider_status"] == "rate_limited"
    assert row.response_payload["cacheable"] is False
    assert clock.sleeps == [0.5, 0.5]


def test_generic_error_does_not_retry(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_error", fixture_outcome(outcome_fixtures, "error")
    )
    clock = FakeClock()
    pipeline = _forced_pipeline(
        session,
        tmp_path,
        provider,
        reliability=SHIPPED_RELIABILITY,
        clock=clock,
        use_cache=False,
    )
    row = pipeline.research_case(sample_case.id)
    assert provider.calls == 1
    assert row.status is ResearchStatus.ERROR
    assert clock.sleeps == []


def test_error_retryable_retries(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = SequenceProvider(
        "fixture_retryable",
        [
            fixture_outcome(outcome_fixtures, "error_retryable"),
            fixture_outcome(outcome_fixtures, "success"),
        ],
    )
    clock = FakeClock()
    pipeline = _forced_pipeline(
        session,
        tmp_path,
        provider,
        reliability=SHIPPED_RELIABILITY,
        clock=clock,
        use_cache=False,
    )
    row = pipeline.research_case(sample_case.id)
    assert provider.calls == 2
    assert row.status is ResearchStatus.SUCCESS
    assert clock.sleeps == [0.5]


def test_credentials_missing_does_not_retry(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY", raising=False)
    clock = FakeClock()
    registry = registry_from_yaml(
        tmp_path,
        reliability=SHIPPED_RELIABILITY,
        providers={
            "manual_lookup": {"type": "manual", "description": "t"},
            "null": {"type": "null", "description": "t"},
            "example_open_data": {
                "type": "credentials_required",
                "requires_credential": "SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY",
            },
        },
    )
    force_provider(registry, "example_open_data")
    pipeline = ResearchPipeline(
        session,
        registry=registry,
        use_cache=False,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
    )
    row = pipeline.research_case(sample_case.id)
    assert row.status is ResearchStatus.ERROR
    assert row.response_payload["error_code"] == "credentials_missing"
    assert clock.sleeps == []


def test_null_provider_does_not_retry(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
) -> None:
    clock = FakeClock()
    registry = registry_from_yaml(tmp_path, reliability=SHIPPED_RELIABILITY)
    force_provider(registry, "null")
    pipeline = ResearchPipeline(
        session,
        registry=registry,
        use_cache=False,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
    )
    row = pipeline.research_case(sample_case.id)
    assert row.status is ResearchStatus.NOT_FOUND
    assert row.response_payload["provider_status"] == "skipped"
    assert clock.sleeps == []


def test_local_limiter_paces_without_rate_limited_row(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "not_found")
    )
    clock = FakeClock()
    reliability = {
        "cache": {"ttl_seconds": 0},
        "retry": {"max_attempts": 1, "backoff_seconds": 0.5},
        "rate_limit": {"per_second": 10},
    }
    pipeline = _forced_pipeline(
        session,
        tmp_path,
        provider,
        reliability=reliability,
        clock=clock,
        use_cache=False,
    )
    first = pipeline.research_case(sample_case.id)
    second = pipeline.research_case(sample_case.id)
    assert provider.calls == 2
    assert first.id != second.id
    assert first.response_payload["provider_status"] == "not_found"
    assert second.response_payload["provider_status"] == "not_found"
    assert clock.sleeps == [0.1]
    statuses = session.scalars(
        select(ResearchResult.status).where(
            ResearchResult.surplus_case_id == sample_case.id
        )
    ).all()
    assert statuses == [ResearchStatus.NOT_FOUND, ResearchStatus.NOT_FOUND]


def test_pending_selector_unchanged_after_research(
    session: Session, sample_case: SurplusCase
) -> None:
    from surplus_ai.research.candidate import CandidateSelector

    pipeline = ResearchPipeline(session)
    pipeline.research_case(sample_case.id)
    pending = CandidateSelector(session).pending(state="MD", limit=10)
    assert any(c.surplus_case_id == sample_case.id for c in pending)


def test_pipeline_cache_path_has_no_contact_lead_property_compliance_writes(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "success")
    )
    pipeline = _forced_pipeline(
        session, tmp_path, provider, reliability=SHIPPED_RELIABILITY
    )
    pipeline.research_case(sample_case.id)
    pipeline.research_case(sample_case.id)
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Property)) == 0
    assert session.scalar(select(func.count()).select_from(ComplianceEvaluation)) == 0


def test_cli_exposes_no_cache_flag() -> None:
    import inspect

    from surplus_ai.cli.commands.research import research_case, research_pending

    assert "no_cache" in inspect.signature(research_case).parameters
    assert "no_cache" in inspect.signature(research_pending).parameters
