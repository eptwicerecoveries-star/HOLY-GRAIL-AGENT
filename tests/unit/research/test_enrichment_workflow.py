"""Phase 6E-B: workflow integration of apply_research_result into pipeline + review resolve."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.enums import (
    ResearchReviewReason,
    ResearchReviewResolution,
    ResearchStatus,
    ReviewStatus,
)
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.owner import Owner
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.enrichment import (
    EnrichmentApplyResult,
    EnrichmentBlockReason,
    attempt_workflow_enrichment,
)
from surplus_ai.research.models import (
    EvidenceAtom,
    ProviderOutcome,
    ProviderOutcomeStatus,
    ResearchMethod,
)
from surplus_ai.research.pipeline import ResearchPipeline
from surplus_ai.research.review import ResearchReviewQueue
from tests.unit.research.conftest import (
    SHIPPED_RELIABILITY,
    FixtureProvider,
    force_provider,
    registry_from_yaml,
)

_RETRIEVED = datetime(2026, 8, 16, 18, 0, tzinfo=UTC)


def _atom(field: str, value: str, *, confidence: float = 0.95) -> EvidenceAtom:
    return EvidenceAtom(
        field=field,
        original_value=value,
        normalized_value=value,
        source="fixture_workflow",
        source_url="https://example.invalid/parcels/x",
        retrieved_at=_RETRIEVED,
        confidence=confidence,
        method=ResearchMethod.OPEN_DATA,
        requires_human_verification=False,
        owner_type_context=None,
    )


def _outcome(
    *,
    status: ProviderOutcomeStatus = ProviderOutcomeStatus.SUCCESS,
    found: bool = True,
    requires_human_review: bool = False,
    evidence: tuple[EvidenceAtom, ...] = (),
    cacheable: bool = True,
    error_code: str | None = None,
) -> ProviderOutcome:
    return ProviderOutcome(
        status=status,
        found=found,
        requires_human_review=requires_human_review,
        evidence=evidence,
        cacheable=cacheable,
        error_code=error_code,
        notes="Phase 6E-B fixture — no network.",
        raw_response={"provider_id": "fixture_workflow"},
    )


def _pipeline(
    session: Session,
    tmp_path: Any,
    outcome: ProviderOutcome,
    *,
    use_cache: bool = True,
) -> ResearchPipeline:
    provider = FixtureProvider("fixture_workflow", outcome)
    registry = registry_from_yaml(
        tmp_path,
        reliability=SHIPPED_RELIABILITY,
        providers={
            "manual_lookup": {"type": "manual", "description": "test"},
            "null": {"type": "null", "description": "test"},
            "fixture_workflow": {"type": "manual", "description": "workflow fixture"},
        },
    )
    registry.register(provider)
    force_provider(registry, provider.name)
    return ResearchPipeline(session, registry=registry, use_cache=use_cache)


def _extra_pending_review(
    session: Session,
    row: ResearchResult,
    *,
    reason: ResearchReviewReason = ResearchReviewReason.COMPLEX_OWNER_CONTEXT,
) -> ResearchReviewItem:
    item = ResearchReviewItem(
        surplus_case_id=row.surplus_case_id,
        research_result_id=row.id,
        provider=row.provider,
        reason=reason,
        reason_detail="extra pending for aggregate tests",
        status=ReviewStatus.PENDING,
    )
    session.add(item)
    session.flush()
    return item


def test_new_success_no_review_applies_parcel(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(evidence=(_atom("parcel_id", "WF-PARCEL-1"),)),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    assert row.status is ResearchStatus.SUCCESS
    session.refresh(sample_case)
    assert sample_case.parcel_id == "WF-PARCEL-1"
    assert session.scalar(select(func.count()).select_from(Property)) == 0


def test_new_success_conflict_does_not_overwrite(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = "KEEP-LOCAL"
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(evidence=(_atom("parcel_id", "OTHER-EVIDENCE"),)),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    assert row.id is not None
    session.refresh(sample_case)
    assert sample_case.parcel_id == "KEEP-LOCAL"


def test_new_not_found_no_enrichment(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            status=ProviderOutcomeStatus.NOT_FOUND,
            found=False,
            requires_human_review=False,
            evidence=(),
            cacheable=True,
        ),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    assert row.status is ResearchStatus.NOT_FOUND
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_new_error_no_enrichment(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            status=ProviderOutcomeStatus.ERROR,
            found=False,
            requires_human_review=False,
            evidence=(),
            cacheable=False,
            error_code="upstream_error",
        ),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    assert row.status is ResearchStatus.ERROR
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_new_empty_evidence_no_enrichment(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(evidence=()),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    assert row.status is ResearchStatus.SUCCESS
    assert row.response_payload["evidence"] == []
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_unsupported_evidence_only_persists_without_mutation(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    owners = list(session.scalars(select(Owner).where(Owner.surplus_case_id == sample_case.id)))
    original_name = owners[0].raw_name
    original_parcel = sample_case.parcel_id
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            evidence=(
                _atom("mailing_address", "PO BOX 9"),
                _atom("owner_name_on_record", "GIS OWNER"),
                _atom("account_id", "A-9"),
            )
        ),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    assert row.status is ResearchStatus.SUCCESS
    session.refresh(sample_case)
    session.refresh(owners[0])
    assert sample_case.parcel_id == original_parcel
    assert owners[0].raw_name == original_name
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Property)) == 0


def test_review_required_persists_and_enqueues_without_apply(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "NEED-REVIEW"),),
        ),
    )
    with patch(
        "surplus_ai.research.pipeline.attempt_workflow_enrichment"
    ) as mocked:
        row = pipeline.research_case(sample_case.id, use_cache=False)
        mocked.assert_not_called()
    items = list(session.scalars(select(ResearchReviewItem)).all())
    assert len(items) == 1
    assert items[0].research_result_id == row.id
    assert items[0].status is ReviewStatus.PENDING
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_resolve_usable_only_review_applies(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "AFTER-REVIEW"),),
        ),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.research_result_id == row.id
    ResearchReviewQueue(session).resolve(
        item.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    session.refresh(sample_case)
    assert sample_case.parcel_id == "AFTER-REVIEW"


def test_usable_plus_pending_resolve_usable_does_not_enrich(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "STILL-PENDING"),),
        ),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    first = session.scalar(select(ResearchReviewItem))
    assert first is not None
    _extra_pending_review(session, row)
    ResearchReviewQueue(session).resolve(
        first.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_usable_plus_usable_final_resolve_enriches(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "BOTH-USABLE"),),
        ),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    first = session.scalar(select(ResearchReviewItem))
    assert first is not None
    second = _extra_pending_review(session, row)
    queue = ResearchReviewQueue(session)
    queue.resolve(
        first.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    session.refresh(sample_case)
    assert sample_case.parcel_id is None
    queue.resolve(
        second.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    session.refresh(sample_case)
    assert sample_case.parcel_id == "BOTH-USABLE"


def test_usable_plus_conflict_no_enrichment(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "CONFLICT-GATE"),),
        ),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    first = session.scalar(select(ResearchReviewItem))
    assert first is not None
    second = _extra_pending_review(session, row)
    queue = ResearchReviewQueue(session)
    queue.resolve(
        first.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    queue.resolve(
        second.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.CONFLICT_UNRESOLVED,
    )
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


@pytest.mark.parametrize(
    "resolution",
    [
        ResearchReviewResolution.EVIDENCE_INSUFFICIENT,
        ResearchReviewResolution.NEEDS_ADDITIONAL_RESEARCH,
        ResearchReviewResolution.CONFLICT_UNRESOLVED,
    ],
)
def test_non_usable_resolve_does_not_call_apply(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    resolution: ResearchReviewResolution,
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "NO-APPLY"),),
        ),
    )
    pipeline.research_case(sample_case.id, use_cache=False)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    with patch(
        "surplus_ai.research.review.attempt_workflow_enrichment"
    ) as mocked:
        ResearchReviewQueue(session).resolve(
            item.id,
            reviewed_by="reviewer",
            resolution=resolution,
        )
        mocked.assert_not_called()
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_not_relevant_reject_does_not_call_apply(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "REJECTED"),),
        ),
    )
    pipeline.research_case(sample_case.id, use_cache=False)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    with patch(
        "surplus_ai.research.review.attempt_workflow_enrichment"
    ) as mocked:
        ResearchReviewQueue(session).resolve(item.id, reviewed_by="reviewer", reject=True)
        mocked.assert_not_called()
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_other_result_review_cannot_enrich_target(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    target_pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "TARGET-ONLY"),),
        ),
        use_cache=False,
    )
    target = target_pipeline.research_case(sample_case.id, use_cache=False)
    other = ResearchResult(
        surplus_case_id=sample_case.id,
        provider="other_provider",
        request_payload={"schema_version": 1, "provider": "other_provider"},
        response_payload={
            "schema_version": 1,
            "found": True,
            "requires_human_review": True,
            "evidence": [
                {
                    "field": "mailing_address",
                    "original_value": "PO BOX OTHER",
                    "normalized_value": "PO BOX OTHER",
                    "source": "x",
                    "source_url": None,
                    "retrieved_at": _RETRIEVED.isoformat(),
                    "confidence": 0.9,
                    "method": "open_data",
                    "requires_human_verification": False,
                    "owner_type_context": None,
                }
            ],
            "provider_status": "success",
            "cacheable": True,
            "error_code": None,
            "error_detail": None,
            "notes": None,
            "source_url": None,
            "raw_response": {},
        },
        status=ResearchStatus.SUCCESS,
    )
    session.add(other)
    session.flush()
    other_item = ResearchReviewItem(
        surplus_case_id=sample_case.id,
        research_result_id=other.id,
        provider=other.provider,
        reason=ResearchReviewReason.AMBIGUOUS_IDENTITY,
        reason_detail="other result",
        status=ReviewStatus.PENDING,
    )
    session.add(other_item)
    session.flush()
    ResearchReviewQueue(session).resolve(
        other_item.id,
        reviewed_by="reviewer",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    session.refresh(sample_case)
    assert sample_case.parcel_id is None
    target_item = session.scalar(
        select(ResearchReviewItem).where(
            ResearchReviewItem.research_result_id == target.id
        )
    )
    assert target_item is not None
    assert target_item.status is ReviewStatus.PENDING


def test_cache_hit_does_not_attempt_enrichment(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    # Keep parcel stable so cache_key matches across lookups.
    sample_case.parcel_id = "01-234567"
    session.flush()
    outcome = _outcome(evidence=(_atom("parcel_id", "01-234567"),))
    pipeline = _pipeline(session, tmp_path, outcome, use_cache=True)
    first = pipeline.research_case(sample_case.id)
    with patch(
        "surplus_ai.research.pipeline.attempt_workflow_enrichment"
    ) as mocked:
        second = pipeline.research_case(sample_case.id)
        mocked.assert_not_called()
    assert second.id == first.id
    count = session.scalar(
        select(func.count())
        .select_from(ResearchResult)
        .where(ResearchResult.surplus_case_id == sample_case.id)
    )
    assert count == 1
    assert session.scalar(select(func.count()).select_from(ResearchReviewItem)) == 0


def test_idempotent_second_explicit_apply_after_workflow(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    prop = Property(surplus_case_id=sample_case.id, parcel_id=None)
    session.add(prop)
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(evidence=(_atom("parcel_id", "ONCE-WF"),)),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    session.refresh(prop)
    first_ts = prop.last_researched_at
    assert first_ts is not None
    second = attempt_workflow_enrichment(
        session,
        case_id=sample_case.id,
        research_result_id=row.id,
        trigger="explicit_retry",
    )
    assert second.applied is False
    assert "surplus_case.parcel_id" in second.already_present_fields
    session.refresh(prop)
    assert prop.last_researched_at == first_ts
    assert (
        session.scalar(
            select(func.count()).select_from(Property).where(
                Property.surplus_case_id == sample_case.id
            )
        )
        == 1
    )
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_apply_exception_on_resolve_leaves_session_for_caller_rollback(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = None
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(
            requires_human_review=True,
            evidence=(_atom("parcel_id", "TXN"),),
        ),
    )
    pipeline.research_case(sample_case.id, use_cache=False)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    item_id = item.id

    def _boom(*_a: Any, **_k: Any) -> EnrichmentApplyResult:
        raise RuntimeError("simulated enrichment failure")

    with (
        patch("surplus_ai.research.review.attempt_workflow_enrichment", side_effect=_boom),
        pytest.raises(RuntimeError, match="simulated enrichment failure"),
    ):
        with session.begin_nested():
            ResearchReviewQueue(session).resolve(
                item_id,
                reviewed_by="reviewer",
                resolution=ResearchReviewResolution.EVIDENCE_USABLE,
            )
    recovered = session.get(ResearchReviewItem, item_id)
    assert recovered is not None
    assert recovered.status is ReviewStatus.PENDING
    session.refresh(sample_case)
    assert sample_case.parcel_id is None

def test_blocked_apply_does_not_invalidate_persisted_result(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    sample_case.parcel_id = "KEEP"
    session.flush()
    pipeline = _pipeline(
        session,
        tmp_path,
        _outcome(evidence=(_atom("parcel_id", "OTHER"),)),
    )
    row = pipeline.research_case(sample_case.id, use_cache=False)
    assert session.get(ResearchResult, row.id) is not None
    session.refresh(sample_case)
    assert sample_case.parcel_id == "KEEP"


def test_no_compliance_mutation_on_workflow_apply(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    before = session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    sample_case.parcel_id = None
    session.flush()
    _pipeline(
        session,
        tmp_path,
        _outcome(evidence=(_atom("parcel_id", "SAFE"),)),
    ).research_case(sample_case.id, use_cache=False)
    assert (
        session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    ) == before


def test_workflow_helper_returns_block_reason_for_review_required(
    session: Session, sample_case: SurplusCase
) -> None:
    row = ResearchResult(
        surplus_case_id=sample_case.id,
        provider="manual_lookup",
        request_payload={"schema_version": 1, "provider": "manual_lookup"},
        response_payload={
            "schema_version": 1,
            "found": True,
            "requires_human_review": True,
            "evidence": [
                {
                    "field": "parcel_id",
                    "original_value": "X",
                    "normalized_value": "X",
                    "source": "t",
                    "source_url": None,
                    "retrieved_at": _RETRIEVED.isoformat(),
                    "confidence": 0.9,
                    "method": "open_data",
                    "requires_human_verification": False,
                    "owner_type_context": None,
                }
            ],
            "provider_status": "success",
            "cacheable": True,
            "error_code": None,
            "error_detail": None,
            "notes": None,
            "source_url": None,
            "raw_response": {},
        },
        status=ResearchStatus.SUCCESS,
    )
    session.add(row)
    session.flush()
    result = attempt_workflow_enrichment(
        session,
        case_id=sample_case.id,
        research_result_id=row.id,
        trigger="unit",
    )
    assert result.blocked is True
    assert result.block_reason is EnrichmentBlockReason.HUMAN_REVIEW_REQUIRED
