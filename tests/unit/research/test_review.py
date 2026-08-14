from __future__ import annotations

import inspect
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.enums import (
    ResearchReviewReason,
    ResearchReviewResolution,
    ReviewStatus,
    SurplusCaseStatus,
)
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.exceptions import ResearchReviewError
from surplus_ai.research.pipeline import ResearchPipeline
from surplus_ai.research.review import ResearchReviewQueue, classify_review_reason
from tests.unit.research.conftest import (
    SHIPPED_RELIABILITY,
    FakeClock,
    FixtureProvider,
    fixture_outcome,
    force_provider,
    registry_from_yaml,
)


def _pipeline(
    session: Session,
    tmp_path: Any,
    provider: FixtureProvider,
    *,
    reliability: dict[str, Any] | None = None,
    use_cache: bool = True,
    clock: FakeClock | None = None,
) -> ResearchPipeline:
    registry = registry_from_yaml(tmp_path, reliability=reliability or SHIPPED_RELIABILITY)
    registry.register(provider)
    force_provider(registry, provider.name)
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs = {
            "monotonic": clock.monotonic,
            "sleeper": clock.sleep,
            "wall_clock": clock.wall,
        }
    return ResearchPipeline(session, registry=registry, use_cache=use_cache, **kwargs)


def _count_reviews(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(ResearchReviewItem)) or 0)


def test_requires_human_review_persist_creates_pending(
    session: Session, sample_case: SurplusCase
) -> None:
    row = ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    assert row.response_payload["requires_human_review"] is True
    items = list(session.scalars(select(ResearchReviewItem)).all())
    assert len(items) == 1
    assert items[0].status is ReviewStatus.PENDING
    assert items[0].research_result_id == row.id
    assert items[0].surplus_case_id == sample_case.id
    assert items[0].reason is ResearchReviewReason.MANUAL_RESEARCH_REQUIRED


def test_requires_human_review_false_does_not_enqueue(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "success")
    )
    _pipeline(session, tmp_path, provider, use_cache=False).research_case(sample_case.id)
    assert _count_reviews(session) == 0


def test_repeat_persist_does_not_duplicate_pending(
    session: Session, sample_case: SurplusCase
) -> None:
    pipeline = ResearchPipeline(session)
    first = pipeline.research_case(sample_case.id, use_cache=False)
    second = pipeline.research_case(sample_case.id, use_cache=False)
    assert first.id != second.id
    items = list(session.scalars(select(ResearchReviewItem)).all())
    assert len(items) == 1
    assert items[0].research_result_id == first.id


def test_cache_hit_does_not_duplicate_review(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "not_found")
    )
    pipeline = _pipeline(session, tmp_path, provider, reliability=SHIPPED_RELIABILITY)
    pipeline.research_case(sample_case.id)
    pipeline.research_case(sample_case.id)
    assert provider.calls == 1
    assert _count_reviews(session) == 1


def test_no_cache_repeat_does_not_duplicate_while_pending(
    session: Session, sample_case: SurplusCase
) -> None:
    pipeline = ResearchPipeline(session)
    pipeline.research_case(sample_case.id, use_cache=False)
    pipeline.research_case(sample_case.id, use_cache=False)
    assert _count_reviews(session) == 1


def test_close_then_new_persist_creates_new_pending_row(
    session: Session, sample_case: SurplusCase
) -> None:
    pipeline = ResearchPipeline(session)
    first = pipeline.research_case(sample_case.id, use_cache=False)
    queue = ResearchReviewQueue(session)
    original = queue.list_items()[1][0]
    queue.resolve(
        original.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    original_id = original.id
    original_result_id = original.research_result_id
    pipeline.research_case(sample_case.id, use_cache=False)
    items = list(
        session.scalars(select(ResearchReviewItem).order_by(ResearchReviewItem.created_at)).all()
    )
    assert len(items) == 2
    closed = session.get(ResearchReviewItem, original_id)
    assert closed is not None
    assert closed.status is ReviewStatus.RESOLVED
    assert closed.research_result_id == original_result_id == first.id
    assert items[1].status is ReviewStatus.PENDING
    assert items[1].id != original_id
    assert items[1].research_result_id != first.id


def test_different_provider_creates_separate_pending(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "not_found")
    )
    _pipeline(session, tmp_path, provider, use_cache=False).research_case(sample_case.id)
    assert _count_reviews(session) == 2


def test_pending_to_resolved_and_rejected(
    session: Session, sample_case: SurplusCase
) -> None:
    ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    queue = ResearchReviewQueue(session)
    item = queue.list_items()[1][0]
    closed = queue.resolve(
        item.id,
        reviewed_by="alice",
        notes="usable as evidence only",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    assert closed.status is ReviewStatus.RESOLVED
    assert closed.resolution is ResearchReviewResolution.EVIDENCE_USABLE
    assert closed.reviewed_by == "alice"
    assert closed.reviewer_notes == "usable as evidence only"
    assert closed.reviewed_at is not None
    assert "evidence_confirmed" not in {member.value for member in ResearchReviewResolution}

    ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    second = [i for i in queue.list_items()[1] if i.status is ReviewStatus.PENDING][0]
    rejected = queue.resolve(second.id, reviewed_by="bob", reject=True)
    assert rejected.status is ReviewStatus.REJECTED
    assert rejected.resolution is ResearchReviewResolution.NOT_RELEVANT


def test_terminal_transition_fails_and_does_not_overwrite(
    session: Session, sample_case: SurplusCase
) -> None:
    ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    queue = ResearchReviewQueue(session)
    item = queue.list_items()[1][0]
    first = queue.resolve(
        item.id,
        reviewed_by="alice",
        notes="first close",
        resolution=ResearchReviewResolution.EVIDENCE_INSUFFICIENT,
    )
    with pytest.raises(ResearchReviewError, match="already resolved"):
        queue.resolve(
            item.id,
            reviewed_by="mallory",
            notes="overwrite attempt",
            resolution=ResearchReviewResolution.EVIDENCE_USABLE,
        )
    session.refresh(first)
    assert first.reviewed_by == "alice"
    assert first.reviewer_notes == "first close"
    assert first.resolution is ResearchReviewResolution.EVIDENCE_INSUFFICIENT
    assert first.status is ReviewStatus.RESOLVED


def test_empty_reviewed_by_rejected(
    session: Session, sample_case: SurplusCase
) -> None:
    ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    queue = ResearchReviewQueue(session)
    item = queue.list_items()[1][0]
    with pytest.raises(ResearchReviewError, match="reviewed_by"):
        queue.resolve(
            item.id,
            reviewed_by="   ",
            resolution=ResearchReviewResolution.EVIDENCE_USABLE,
        )
    session.refresh(item)
    assert item.status is ReviewStatus.PENDING


def test_frozen_research_result_id_and_newer_warning(
    session: Session, sample_case: SurplusCase
) -> None:
    pipeline = ResearchPipeline(session)
    first = pipeline.research_case(sample_case.id, use_cache=False)
    pipeline.research_case(sample_case.id, use_cache=False)
    queue = ResearchReviewQueue(session)
    shown = queue.show(queue.list_items()[1][0].id)
    assert shown.item.research_result_id == first.id
    assert shown.triggering.id == first.id
    assert shown.newer is not None
    assert shown.newer.id != first.id
    session.refresh(shown.item)
    assert shown.item.research_result_id == first.id


def test_resolve_does_not_mutate_result_or_case_or_side_tables(
    session: Session, sample_case: SurplusCase
) -> None:
    before_status = sample_case.status
    row = ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    fetched = row.fetched_at
    payload = dict(row.response_payload or {})
    queue = ResearchReviewQueue(session)
    item = queue.list_items()[1][0]
    queue.resolve(
        item.id,
        reviewed_by="alice",
        resolution=ResearchReviewResolution.NEEDS_ADDITIONAL_RESEARCH,
    )
    session.refresh(row)
    session.refresh(sample_case)
    assert row.fetched_at == fetched
    assert row.response_payload == payload
    assert sample_case.status is before_status is SurplusCaseStatus.NORMALIZED
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Property)) == 0
    assert session.scalar(select(func.count()).select_from(ComplianceEvaluation)) == 0


def test_reason_detail_is_structured_not_notes(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    outcome = fixture_outcome(outcome_fixtures, "error")
    provider = FixtureProvider("fixture_error", outcome)
    _pipeline(session, tmp_path, provider, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    detail = item.reason_detail or ""
    assert "Fixture simulated provider failure." not in detail
    assert "raw_response" not in detail
    assert "unavailable" not in detail
    assert "error_code=upstream_error" in detail
    assert "provider_status=error" in detail
    assert "SUPER_SECRET" not in detail


def test_manual_reason_uses_configured_type_not_provider_id(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    registry = registry_from_yaml(
        tmp_path,
        reliability=SHIPPED_RELIABILITY,
        providers={
            "human_desk": {"type": "manual", "description": "renamed manual"},
            "null": {"type": "null", "description": "t"},
        },
    )
    force_provider(registry, "human_desk")
    row = ResearchPipeline(session, registry=registry, use_cache=False).research_case(
        sample_case.id
    )
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.provider == "human_desk"
    assert item.reason is ResearchReviewReason.MANUAL_RESEARCH_REQUIRED
    assert classify_review_reason(row, provider_type="manual") is (
        ResearchReviewReason.MANUAL_RESEARCH_REQUIRED
    )


def test_null_and_credentials_are_provider_unavailable(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY", raising=False)
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
    force_provider(registry, "null")
    ResearchPipeline(session, registry=registry, use_cache=False).research_case(sample_case.id)
    null_item = session.scalar(select(ResearchReviewItem))
    assert null_item is not None
    assert null_item.reason is ResearchReviewReason.PROVIDER_UNAVAILABLE

    session.delete(null_item)
    session.flush()
    force_provider(registry, "example_open_data")
    ResearchPipeline(session, registry=registry, use_cache=False).research_case(sample_case.id)
    cred_item = session.scalars(
        select(ResearchReviewItem).order_by(ResearchReviewItem.created_at.desc())
    ).first()
    assert cred_item is not None
    assert cred_item.reason is ResearchReviewReason.PROVIDER_UNAVAILABLE


def test_provider_failure_reason(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_error", fixture_outcome(outcome_fixtures, "error")
    )
    _pipeline(session, tmp_path, provider, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_FAILURE


def test_complex_owner_context_reason(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
) -> None:
    from surplus_ai.research.models import (
        ProviderOutcome,
        ProviderOutcomeStatus,
        ResearchMethod,
        utc_now,
    )
    from surplus_ai.research.provenance import build_evidence_atom

    atom = build_evidence_atom(
        field="owner_name_on_record",
        original_value="ESTATE OF X",
        normalized_value="ESTATE OF X",
        source="fixture",
        method=ResearchMethod.OPEN_DATA,
        confidence=0.95,
        requires_human_verification=False,
        owner_type_context="estate",
        retrieved_at=utc_now(),
    )
    outcome = ProviderOutcome(
        status=ProviderOutcomeStatus.SUCCESS,
        found=True,
        requires_human_review=True,
        evidence=(atom,),
    )
    provider = FixtureProvider("fixture_estate", outcome)
    _pipeline(session, tmp_path, provider, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.COMPLEX_OWNER_CONTEXT


def test_ambiguous_identity_reason(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "ambiguous_identity")
    )
    _pipeline(session, tmp_path, provider, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.AMBIGUOUS_IDENTITY


def test_limiter_pacing_does_not_create_extra_review_items(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    outcome_fixtures: dict[str, Any],
) -> None:
    provider = FixtureProvider(
        "fixture_open_data", fixture_outcome(outcome_fixtures, "not_found")
    )
    clock = FakeClock()
    pipeline = _pipeline(
        session,
        tmp_path,
        provider,
        reliability={
            "cache": {"ttl_seconds": 0},
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
            "rate_limit": {"per_second": 10},
        },
        use_cache=False,
        clock=clock,
    )
    pipeline.research_case(sample_case.id)
    pipeline.research_case(sample_case.id)
    assert clock.sleeps == [0.1]
    assert _count_reviews(session) == 1


def test_partial_unique_index_blocks_duplicate_pending(
    session: Session, sample_case: SurplusCase
) -> None:
    row = ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(
                ResearchReviewItem(
                    surplus_case_id=sample_case.id,
                    research_result_id=row.id,
                    provider=item.provider,
                    reason=item.reason,
                    status=ReviewStatus.PENDING,
                )
            )
            session.flush()
    session.expire_all()
    assert _count_reviews(session) == 1


def test_enqueue_integrity_error_uses_savepoint(
    session: Session, sample_case: SurplusCase
) -> None:
    row = ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    queue = ResearchReviewQueue(session)
    existing = queue.list_items()[1][0]
    original = queue._find_pending

    def hide_once(*args: Any, **kwargs: Any) -> ResearchReviewItem | None:
        hide_once.calls += 1  # type: ignore[attr-defined]
        if hide_once.calls == 1:  # type: ignore[attr-defined]
            return None
        return original(*args, **kwargs)

    hide_once.calls = 0  # type: ignore[attr-defined]
    queue._find_pending = hide_once  # type: ignore[method-assign]
    recovered = queue.enqueue_for_result(row)
    assert recovered is not None
    assert recovered.id == existing.id
    assert session.scalar(select(func.count()).select_from(ResearchResult)) == 1
    assert _count_reviews(session) == 1


def test_case_delete_cascades_review_items(
    session: Session, sample_case: SurplusCase
) -> None:
    ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    assert _count_reviews(session) == 1
    session.delete(sample_case)
    session.flush()
    assert _count_reviews(session) == 0


def test_direct_result_delete_blocked_while_review_depends(
    session: Session, sample_case: SurplusCase
) -> None:
    row = ResearchPipeline(session).research_case(sample_case.id, use_cache=False)
    session.delete(row)
    with pytest.raises(IntegrityError):
        session.flush()


def test_no_legal_entitlement_fields_on_review_model() -> None:
    names = set(ResearchReviewItem.__table__.columns.keys())
    forbidden = {
        "entitled",
        "entitlement",
        "claimant",
        "heir",
        "contactable",
        "outreach",
        "compliant",
    }
    assert names.isdisjoint(forbidden)
    assert "evidence_confirmed" not in {m.value for m in ResearchReviewResolution}


def test_cli_review_commands_exist() -> None:
    from surplus_ai.cli.commands.research import review_list, review_resolve, review_show

    list_params = inspect.signature(review_list).parameters
    assert "status" in list_params
    assert "state" in list_params
    assert "county" in list_params
    assert "provider" in list_params
    assert "reason" in list_params
    assert "case_id" in list_params
    resolve_params = inspect.signature(review_resolve).parameters
    assert "by" in resolve_params
    assert "notes" in resolve_params
    assert "resolution" in resolve_params
    assert "reject" in resolve_params
    assert "item_id" in inspect.signature(review_show).parameters
