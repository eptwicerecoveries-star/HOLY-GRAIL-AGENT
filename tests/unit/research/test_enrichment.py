from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    ResearchReviewReason,
    ResearchReviewResolution,
    ResearchStatus,
    ReviewStatus,
    SurplusCaseStatus,
    SurplusSourceType,
)
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.owner import Owner
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.enrichment import (
    EnrichmentBlockReason,
    apply_research_result,
)


def _atom(field: str, value: str | None, *, confidence: float = 0.9) -> dict[str, object]:
    return {
        "field": field,
        "original_value": value,
        "normalized_value": value,
        "source": "Example Source",
        "source_url": "https://api.example.gov/v1/parcels",
        "retrieved_at": datetime(2026, 8, 16, 12, 0, tzinfo=UTC).isoformat(),
        "confidence": confidence,
        "method": "open_data",
        "requires_human_verification": False,
        "owner_type_context": None,
    }


def _persist_result(
    session: Session,
    case: SurplusCase,
    *,
    status: ResearchStatus = ResearchStatus.SUCCESS,
    found: bool = True,
    requires_human_review: bool = False,
    evidence: list[dict[str, object]] | None = None,
    provider: str = "example_rest_json",
) -> ResearchResult:
    row = ResearchResult(
        surplus_case_id=case.id,
        provider=provider,
        request_payload={"schema_version": 1, "provider": provider},
        response_payload={
            "schema_version": 1,
            "found": found,
            "requires_human_review": requires_human_review,
            "evidence": evidence if evidence is not None else [_atom("parcel_id", "NEW-PARCEL-1")],
            "provider_status": "success" if status is ResearchStatus.SUCCESS else status.value,
            "cacheable": True,
            "error_code": None,
            "error_detail": None,
            "notes": None,
            "source_url": "https://api.example.gov/v1/parcels",
            "raw_response": {"provider_id": provider, "match_mode": "exact_parcel"},
        },
        status=status,
    )
    session.add(row)
    session.flush()
    return row


def _review_item(
    session: Session,
    row: ResearchResult,
    *,
    status: ReviewStatus = ReviewStatus.RESOLVED,
    resolution: ResearchReviewResolution | None = ResearchReviewResolution.EVIDENCE_USABLE,
    reason: ResearchReviewReason = ResearchReviewReason.AMBIGUOUS_IDENTITY,
) -> ResearchReviewItem:
    item = ResearchReviewItem(
        surplus_case_id=row.surplus_case_id,
        research_result_id=row.id,
        provider=row.provider,
        reason=reason,
        reason_detail="test",
        status=status,
        resolution=resolution,
        reviewed_by="tester" if status is not ReviewStatus.PENDING else None,
        reviewed_at=datetime.now(UTC) if status is not ReviewStatus.PENDING else None,
    )
    session.add(item)
    session.flush()
    return item


def _ensure_property(
    session: Session,
    case: SurplusCase,
    *,
    parcel_id: str | None = None,
    last_researched_at: datetime | None = None,
) -> Property:
    prop = session.scalar(select(Property).where(Property.surplus_case_id == case.id))
    if prop is None:
        prop = Property(surplus_case_id=case.id)
        session.add(prop)
    prop.parcel_id = parcel_id
    prop.last_researched_at = last_researched_at
    session.flush()
    return prop


def _property_count(session: Session, case: SurplusCase) -> int:
    return len(
        list(session.scalars(select(Property).where(Property.surplus_case_id == case.id)))
    )


def test_success_no_review_fills_case_and_existing_property(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    prop = _ensure_property(session, sample_case, parcel_id=None, last_researched_at=None)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "APPLIED-99")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.blocked is False
    assert result.applied is True
    assert "surplus_case.parcel_id" in result.applied_fields
    assert "property.parcel_id" in result.applied_fields
    session.refresh(sample_case)
    session.refresh(prop)
    assert sample_case.parcel_id == "APPLIED-99"
    assert prop.parcel_id == "APPLIED-99"
    assert prop.last_researched_at is not None


def test_no_property_fills_case_only(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = None
    session.flush()
    assert _property_count(session, sample_case) == 0
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "CASE-ONLY")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.applied_fields
    assert "property.parcel_id" in result.unsupported_fields
    session.refresh(sample_case)
    assert sample_case.parcel_id == "CASE-ONLY"
    assert _property_count(session, sample_case) == 0


def test_not_found_blocked(session: Session, sample_case: SurplusCase) -> None:
    row = _persist_result(
        session,
        sample_case,
        status=ResearchStatus.NOT_FOUND,
        found=False,
        evidence=[],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.RESULT_NOT_SUCCESSFUL


def test_error_blocked(session: Session, sample_case: SurplusCase) -> None:
    row = _persist_result(
        session,
        sample_case,
        status=ResearchStatus.ERROR,
        found=False,
        evidence=[],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.RESULT_NOT_SUCCESSFUL


def test_found_false_blocked(session: Session, sample_case: SurplusCase) -> None:
    row = _persist_result(session, sample_case, found=False, evidence=[_atom("parcel_id", "X")])
    row.status = ResearchStatus.SUCCESS
    row.response_payload = {**row.response_payload, "found": False}
    session.flush()
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.RESULT_NOT_FOUND_OUTCOME


def test_empty_evidence_blocked(session: Session, sample_case: SurplusCase) -> None:
    row = _persist_result(session, sample_case, evidence=[])
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.RESULT_HAS_NO_EVIDENCE


def test_case_result_mismatch_blocked(
    session: Session, sample_case: SurplusCase, sample_county: County
) -> None:
    other = SurplusCase(
        county_id=sample_county.id,
        parcel_id="OTHER",
        property_address_raw="9 OTHER ST",
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash="enrichment-other-case",
    )
    session.add(other)
    session.flush()
    row = _persist_result(session, other)
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.CASE_RESULT_MISMATCH


def test_human_review_pending_blocks(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "NEED-REVIEW")],
    )
    _review_item(session, row, status=ReviewStatus.PENDING, resolution=None)
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.HUMAN_REVIEW_REQUIRED
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_human_review_evidence_usable_applies(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "REVIEWED-OK")],
    )
    _review_item(
        session,
        row,
        status=ReviewStatus.RESOLVED,
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.blocked is False
    assert result.applied is True
    session.refresh(sample_case)
    assert sample_case.parcel_id == "REVIEWED-OK"


@pytest.mark.parametrize(
    "resolution",
    [
        ResearchReviewResolution.EVIDENCE_INSUFFICIENT,
        ResearchReviewResolution.NEEDS_ADDITIONAL_RESEARCH,
        ResearchReviewResolution.CONFLICT_UNRESOLVED,
        ResearchReviewResolution.NOT_RELEVANT,
    ],
)
def test_non_usable_resolutions_block(
    session: Session,
    sample_case: SurplusCase,
    resolution: ResearchReviewResolution,
) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "BLOCKED")],
    )
    status = (
        ReviewStatus.REJECTED
        if resolution is ResearchReviewResolution.NOT_RELEVANT
        else ReviewStatus.RESOLVED
    )
    _review_item(session, row, status=status, resolution=resolution)
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.HUMAN_REVIEW_NOT_USABLE
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_usable_plus_conflict_blocks(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "MIXED")],
    )
    _review_item(
        session,
        row,
        status=ReviewStatus.RESOLVED,
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
        reason=ResearchReviewReason.AMBIGUOUS_IDENTITY,
    )
    _review_item(
        session,
        row,
        status=ReviewStatus.RESOLVED,
        resolution=ResearchReviewResolution.CONFLICT_UNRESOLVED,
        reason=ResearchReviewReason.COMPLEX_OWNER_CONTEXT,
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.HUMAN_REVIEW_NOT_USABLE
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_usable_plus_pending_blocks(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "PENDING-MIX")],
    )
    _review_item(
        session,
        row,
        status=ReviewStatus.RESOLVED,
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
        reason=ResearchReviewReason.AMBIGUOUS_IDENTITY,
    )
    _review_item(
        session,
        row,
        status=ReviewStatus.PENDING,
        resolution=None,
        reason=ResearchReviewReason.COMPLEX_OWNER_CONTEXT,
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.block_reason is EnrichmentBlockReason.HUMAN_REVIEW_REQUIRED


def test_usable_plus_usable_eligible(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "DOUBLE-OK")],
    )
    _review_item(
        session,
        row,
        status=ReviewStatus.RESOLVED,
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
        reason=ResearchReviewReason.AMBIGUOUS_IDENTITY,
    )
    _review_item(
        session,
        row,
        status=ReviewStatus.RESOLVED,
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
        reason=ResearchReviewReason.COMPLEX_OWNER_CONTEXT,
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.blocked is False
    assert result.applied is True
    session.refresh(sample_case)
    assert sample_case.parcel_id == "DOUBLE-OK"


def test_review_from_other_result_cannot_authorize(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    target = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "OTHER-AUTH")],
        provider="example_rest_json",
    )
    other = _persist_result(
        session,
        sample_case,
        requires_human_review=True,
        evidence=[_atom("parcel_id", "OTHER-AUTH")],
        provider="example_socrata",
    )
    _review_item(
        session,
        other,
        status=ReviewStatus.RESOLVED,
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=target.id
    )
    assert result.block_reason is EnrichmentBlockReason.HUMAN_REVIEW_REQUIRED
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_identical_existing_value_is_noop(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = "01-234567"
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "01-234567")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.already_present_fields
    session.refresh(sample_case)
    assert sample_case.parcel_id == "01-234567"


def test_conflicting_existing_value_not_overwritten(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = "KEEP-ME"
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "OTHER-VALUE")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.conflicting_fields
    session.refresh(sample_case)
    assert sample_case.parcel_id == "KEEP-ME"


def test_case_conflict_property_empty_fills_property(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = "KEEP-CASE"
    session.flush()
    prop = _ensure_property(session, sample_case, parcel_id=None)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "PROP-FILL")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.conflicting_fields
    assert "property.parcel_id" in result.applied_fields
    session.refresh(sample_case)
    session.refresh(prop)
    assert sample_case.parcel_id == "KEEP-CASE"
    assert prop.parcel_id == "PROP-FILL"


def test_case_empty_property_conflict_fills_case(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    prop = _ensure_property(session, sample_case, parcel_id="KEEP-PROP")
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "CASE-FILL")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.applied_fields
    assert "property.parcel_id" in result.conflicting_fields
    session.refresh(sample_case)
    session.refresh(prop)
    assert sample_case.parcel_id == "CASE-FILL"
    assert prop.parcel_id == "KEEP-PROP"


def test_case_already_equal_no_property_created(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = "SAME-1"
    session.flush()
    assert _property_count(session, sample_case) == 0
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "SAME-1")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.already_present_fields
    assert "property.parcel_id" in result.unsupported_fields
    assert _property_count(session, sample_case) == 0


def test_case_already_equal_property_empty_fills_property(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = "SAME-PROP"
    session.flush()
    prop = _ensure_property(session, sample_case, parcel_id=None)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "SAME-PROP")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.already_present_fields
    assert "property.parcel_id" in result.applied_fields
    session.refresh(sample_case)
    session.refresh(prop)
    assert sample_case.parcel_id == "SAME-PROP"
    assert prop.parcel_id == "SAME-PROP"


def test_both_empty_with_existing_property_fill_both(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    prop = _ensure_property(session, sample_case, parcel_id=None)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "BOTH-1")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.parcel_id" in result.applied_fields
    assert "property.parcel_id" in result.applied_fields
    session.refresh(sample_case)
    session.refresh(prop)
    assert sample_case.parcel_id == "BOTH-1"
    assert prop.parcel_id == "BOTH-1"


def test_last_researched_at_set_only_on_property_parcel_fill(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    fixed = datetime(2020, 1, 1, tzinfo=UTC)
    prop = _ensure_property(session, sample_case, parcel_id=None, last_researched_at=fixed)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "TS-1")],
    )
    apply_research_result(session, case_id=sample_case.id, research_result_id=row.id)
    session.refresh(prop)
    assert prop.last_researched_at is not None
    assert prop.last_researched_at != fixed


def test_last_researched_at_not_changed_when_property_already_equal(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = "EQ-1"
    session.flush()
    fixed = datetime(2021, 2, 2, tzinfo=UTC)
    prop = _ensure_property(session, sample_case, parcel_id="EQ-1", last_researched_at=fixed)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "EQ-1")],
    )
    apply_research_result(session, case_id=sample_case.id, research_result_id=row.id)
    session.refresh(prop)
    assert prop.last_researched_at == fixed


def test_last_researched_at_not_changed_on_property_conflict(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    fixed = datetime(2021, 3, 3, tzinfo=UTC)
    prop = _ensure_property(session, sample_case, parcel_id="KEEP", last_researched_at=fixed)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "OTHER")],
    )
    apply_research_result(session, case_id=sample_case.id, research_result_id=row.id)
    session.refresh(prop)
    assert prop.last_researched_at == fixed


def test_address_only_does_not_set_last_researched_at(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.property_address_raw = None
    session.flush()
    fixed = datetime(2021, 4, 4, tzinfo=UTC)
    prop = _ensure_property(session, sample_case, parcel_id="P-1", last_researched_at=fixed)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("current_address", "500 NEW ST")],
    )
    apply_research_result(session, case_id=sample_case.id, research_result_id=row.id)
    session.refresh(prop)
    assert prop.last_researched_at == fixed


def test_second_apply_does_not_bump_last_researched_at(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    prop = _ensure_property(session, sample_case, parcel_id=None, last_researched_at=None)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "ONCE-TS")],
    )
    apply_research_result(session, case_id=sample_case.id, research_result_id=row.id)
    session.refresh(prop)
    first_ts = prop.last_researched_at
    assert first_ts is not None
    apply_research_result(session, case_id=sample_case.id, research_result_id=row.id)
    session.refresh(prop)
    assert prop.last_researched_at == first_ts


def test_current_address_fill_missing(session: Session, sample_case: SurplusCase) -> None:
    sample_case.property_address_raw = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("current_address", "500 NEW ST")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "surplus_case.property_address_raw" in result.applied_fields
    session.refresh(sample_case)
    assert sample_case.property_address_raw == "500 NEW ST"


def test_owner_name_not_applied_to_owner(
    session: Session, sample_case: SurplusCase
) -> None:
    owners = list(session.scalars(select(Owner).where(Owner.surplus_case_id == sample_case.id)))
    assert owners
    original = owners[0].raw_name
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("owner_name_on_record", "GIS OWNER LLC")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "owner_name_on_record" in result.unsupported_fields
    session.refresh(owners[0])
    assert owners[0].raw_name == original


def test_unsupported_account_and_mailing_reported(
    session: Session, sample_case: SurplusCase
) -> None:
    row = _persist_result(
        session,
        sample_case,
        evidence=[
            _atom("account_id", "A-1"),
            _atom("mailing_address", "PO BOX 1"),
            _atom("property_record_id", "R-1"),
        ],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert set(result.unsupported_fields) >= {
        "account_id",
        "mailing_address",
        "property_record_id",
    }
    assert result.applied is False


def test_whitespace_evidence_not_applied(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "   ")],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "parcel_id" in result.skipped_empty_fields
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_duplicate_same_value_atoms_safe(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[
            _atom("parcel_id", "DUP-1"),
            _atom("parcel_id", "DUP-1"),
        ],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert result.applied is True
    session.refresh(sample_case)
    assert sample_case.parcel_id == "DUP-1"


def test_duplicate_conflicting_atoms_skip_field(
    session: Session, sample_case: SurplusCase
) -> None:
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[
            _atom("parcel_id", "A-1"),
            _atom("parcel_id", "B-2"),
        ],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert "parcel_id" in result.conflicting_evidence_fields
    session.refresh(sample_case)
    assert sample_case.parcel_id is None


def test_idempotent_second_apply(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = None
    session.flush()
    prop = _ensure_property(session, sample_case, parcel_id=None)
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "ONCE-1")],
    )
    first = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    second = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    assert first.applied is True
    assert second.applied is False
    assert "surplus_case.parcel_id" in second.already_present_fields
    assert "property.parcel_id" in second.already_present_fields
    props = list(
        session.scalars(select(Property).where(Property.surplus_case_id == sample_case.id))
    )
    assert len(props) == 1
    assert props[0].id == prop.id


def test_no_lead_contact_compliance_mutation(
    session: Session, sample_case: SurplusCase
) -> None:
    leads_before = session.scalar(select(func.count()).select_from(Lead)) or 0
    contacts_before = session.scalar(select(func.count()).select_from(Contact)) or 0
    compliance_before = (
        session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    )
    sample_case.parcel_id = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[_atom("parcel_id", "SAFE-1")],
    )
    props_before = session.scalar(select(func.count()).select_from(Property)) or 0
    apply_research_result(session, case_id=sample_case.id, research_result_id=row.id)
    assert (session.scalar(select(func.count()).select_from(Lead)) or 0) == leads_before
    assert (session.scalar(select(func.count()).select_from(Contact)) or 0) == contacts_before
    assert (
        session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    ) == compliance_before
    assert (session.scalar(select(func.count()).select_from(Property)) or 0) == props_before


def test_missing_result_blocked(session: Session, sample_case: SurplusCase) -> None:
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=uuid.uuid4()
    )
    assert result.block_reason is EnrichmentBlockReason.RESULT_NOT_FOUND


def test_report_omits_sensitive_values(session: Session, sample_case: SurplusCase) -> None:
    sample_case.parcel_id = None
    sample_case.property_address_raw = None
    session.flush()
    row = _persist_result(
        session,
        sample_case,
        evidence=[
            _atom("parcel_id", "SECRET-PARCEL"),
            _atom("current_address", "999 HIDDEN AVE"),
            _atom("owner_name_on_record", "SECRET OWNER"),
        ],
    )
    result = apply_research_result(
        session, case_id=sample_case.id, research_result_id=row.id
    )
    blob = repr(result)
    assert "SECRET-PARCEL" not in blob
    assert "999 HIDDEN AVE" not in blob
    assert "SECRET OWNER" not in blob
