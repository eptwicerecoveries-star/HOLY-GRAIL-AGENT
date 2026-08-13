from __future__ import annotations

from typing import Any

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
from tests.unit.research.conftest import FixtureProvider, fixture_outcome


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
    pipeline.research_case(sample_case.id)
    pipeline.research_case(sample_case.id)
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
