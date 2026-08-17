"""Phase 6F-B: explicit Lead-bound offline skip-trace workflow."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    ClassificationMethod,
    ContactType,
    CountySourceType,
    LeadStatus,
    OwnerType,
    PublishingFrequency,
    SurplusCaseStatus,
    SurplusSourceType,
)
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.owner import Owner
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.skip_trace.materialize import MaterializeBlockReason
from surplus_ai.skip_trace.models import (
    ContactCandidate,
    ContactCandidateType,
    SkipTraceLookupResult,
    SkipTraceStatus,
)
from surplus_ai.skip_trace.providers.credentials_missing import CredentialsMissingSkipTraceProvider
from surplus_ai.skip_trace.providers.fake import FakeSkipTraceProvider
from surplus_ai.skip_trace.providers.manual import ManualSkipTraceProvider
from surplus_ai.skip_trace.providers.null import NullSkipTraceProvider
from surplus_ai.skip_trace.workflow import (
    SkipTraceWorkflowBlockReason,
    run_skip_trace_for_lead,
)


def _county(session: Session, slug: str = "harford-6fb") -> County:
    county = County(
        slug=slug,
        name="Harford County",
        state="MD",
        fips_code="24025",
        source_type=CountySourceType.MANUAL_UPLOAD,
        parsing_profile_key="md-harford",
        compliance_state_ref="MD",
        publishing_frequency=PublishingFrequency.IRREGULAR,
        is_active=True,
    )
    session.add(county)
    session.flush()
    return county


def _case(session: Session, county: County, *, dedupe: str) -> SurplusCase:
    case = SurplusCase(
        county_id=county.id,
        parcel_id="01-234567",
        property_address_raw="123 MAIN ST",
        sale_date=date(2024, 3, 1),
        surplus_amount=Decimal("5000.00"),
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash=dedupe,
    )
    session.add(case)
    session.flush()
    session.add(
        Owner(
            surplus_case_id=case.id,
            raw_name="STEFFEN, DEBORAH",
            owner_type=OwnerType.INDIVIDUAL,
            classification_confidence=0.8,
            classification_method=ClassificationMethod.RULE,
        )
    )
    session.flush()
    return case


def _lead(session: Session, case: SurplusCase) -> Lead:
    owner = session.scalar(select(Owner).where(Owner.surplus_case_id == case.id))
    lead = Lead(
        surplus_case_id=case.id,
        owner_id=owner.id if owner else None,
        status=LeadStatus.QUALIFIED,
    )
    session.add(lead)
    session.flush()
    return lead


def _phone(
    *,
    raw: str = "555-555-0100",
    case_id: uuid.UUID | None = None,
    requires_human_review: bool = False,
) -> ContactCandidate:
    return ContactCandidate(
        provider_id="fake_skip_trace",
        candidate_type=ContactCandidateType.PHONE,
        raw_value=raw,
        surplus_case_id=case_id,
        confidence=0.9,
        requires_human_review=requires_human_review,
    )


def _email(
    *,
    raw: str = "person@example.com",
    case_id: uuid.UUID | None = None,
    requires_human_review: bool = False,
) -> ContactCandidate:
    return ContactCandidate(
        provider_id="fake_skip_trace",
        candidate_type=ContactCandidateType.EMAIL,
        raw_value=raw,
        surplus_case_id=case_id,
        confidence=0.85,
        requires_human_review=requires_human_review,
    )


def test_fake_provider_end_to_end_materializes_contacts(session: Session) -> None:
    county = _county(session)
    case = _case(session, county, dedupe="6fb-e2e")
    lead = _lead(session, case)
    leads_before = session.scalar(select(func.count()).select_from(Lead)) or 0
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(
            _phone(case_id=case.id),
            _email(case_id=case.id),
        ),
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)

    assert provider.calls == 1
    assert result.completed is True
    assert result.blocked is False
    assert result.provider_status == SkipTraceStatus.SUCCESS.value
    assert result.created_count == 2
    assert result.materialization_attempt_count == 2
    assert len(result.contact_ids) == 2
    assert (session.scalar(select(func.count()).select_from(Lead)) or 0) == leads_before
    contacts = list(session.scalars(select(Contact).where(Contact.lead_id == lead.id)))
    assert len(contacts) == 2
    values = {c.contact_type: c.value for c in contacts}
    assert values[ContactType.PHONE] == "+15555550100"
    assert values[ContactType.EMAIL] == "person@example.com"
    blob = repr(result)
    assert "5555550100" not in blob
    assert "+15555550100" not in blob
    assert "person@example.com" not in blob


def test_missing_lead_blocks_before_provider(session: Session) -> None:
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone(),),
    )
    result = run_skip_trace_for_lead(session, lead_id=uuid.uuid4(), provider=provider)
    assert provider.calls == 0
    assert result.provider_invoked is False
    assert result.block_reason is SkipTraceWorkflowBlockReason.LEAD_NOT_FOUND
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_case_mismatch_candidate_does_not_materialize(session: Session) -> None:
    county = _county(session, slug="harford-6fb-mm")
    case_a = _case(session, county, dedupe="6fb-mm-a")
    case_b = _case(session, county, dedupe="6fb-mm-b")
    lead = _lead(session, case_a)
    # Predetermined lookup result (not Fake candidate stamping) keeps mismatched case id.
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        result=SkipTraceLookupResult(
            status=SkipTraceStatus.SUCCESS,
            provider_id="fake_skip_trace",
            lead_id=lead.id,
            surplus_case_id=case_a.id,
            candidates=(_phone(case_id=case_b.id),),
            requires_human_review=False,
        ),
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert provider.calls == 1
    assert result.materialization_attempt_count == 1
    assert result.blocked_count == 1
    assert result.created_count == 0
    assert result.materialization_results[0].block_reason is (
        MaterializeBlockReason.LEAD_CASE_MISMATCH
    )
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_manual_provider_creates_no_contact(session: Session) -> None:
    county = _county(session, slug="harford-6fb-man")
    case = _case(session, county, dedupe="6fb-man")
    lead = _lead(session, case)
    provider = ManualSkipTraceProvider()
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.provider_invoked is True
    assert result.provider_status == SkipTraceStatus.NOT_FOUND.value
    assert result.provider_error_code == "manual_research_required"
    assert result.block_reason is (
        SkipTraceWorkflowBlockReason.PROVIDER_HUMAN_REVIEW_REQUIRED
    )
    assert result.materialization_attempt_count == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_null_provider_creates_no_contact(session: Session) -> None:
    county = _county(session, slug="harford-6fb-null")
    case = _case(session, county, dedupe="6fb-null")
    lead = _lead(session, case)
    provider = NullSkipTraceProvider()
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.provider_status == SkipTraceStatus.SKIPPED.value
    assert result.provider_error_code == "provider_not_configured"
    assert result.block_reason is (
        SkipTraceWorkflowBlockReason.PROVIDER_HUMAN_REVIEW_REQUIRED
    )
    assert result.materialization_attempt_count == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_credentials_missing_creates_no_contact(session: Session) -> None:
    county = _county(session, slug="harford-6fb-cred")
    case = _case(session, county, dedupe="6fb-cred")
    lead = _lead(session, case)
    provider = CredentialsMissingSkipTraceProvider(
        "example_vendor", "SKIP_TRACE_API_KEY_6FB_TEST"
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.provider_status == SkipTraceStatus.ERROR.value
    assert result.provider_error_code == "credentials_missing"
    assert result.materialization_attempt_count == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_duplicate_candidates_collapse_to_one_contact(session: Session) -> None:
    county = _county(session, slug="harford-6fb-dup")
    case = _case(session, county, dedupe="6fb-dup")
    lead = _lead(session, case)
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(
            _phone(raw="5555550100", case_id=case.id),
            _phone(raw="(555) 555-0100", case_id=case.id),
        ),
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.created_count == 1
    assert result.already_present_count == 1
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_existing_contact_returns_already_present(session: Session) -> None:
    county = _county(session, slug="harford-6fb-exist")
    case = _case(session, county, dedupe="6fb-exist")
    lead = _lead(session, case)
    session.add(
        Contact(
            lead_id=lead.id,
            contact_type=ContactType.PHONE,
            value="+15555550100",
            source="seed",
            is_verified=False,
        )
    )
    session.flush()
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone(raw="5555550100", case_id=case.id),),
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.created_count == 0
    assert result.already_present_count == 1
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_mixed_candidates_partial_materialization(session: Session) -> None:
    county = _county(session, slug="harford-6fb-mix")
    case = _case(session, county, dedupe="6fb-mix")
    lead = _lead(session, case)
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(
            _phone(raw="5555550100", case_id=case.id),
            _phone(raw="not-a-phone", case_id=case.id),
            _email(case_id=case.id, requires_human_review=True),
        ),
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.created_count == 1
    assert result.blocked_count == 2
    assert result.materialization_attempt_count == 3
    assert session.scalar(select(func.count()).select_from(Contact)) == 1
    reasons = {r.block_reason for r in result.materialization_results if r.blocked}
    assert MaterializeBlockReason.INVALID_CONTACT in reasons
    assert MaterializeBlockReason.HUMAN_REVIEW_REQUIRED in reasons


def test_provider_error_skips_materialization(session: Session) -> None:
    county = _county(session, slug="harford-6fb-err")
    case = _case(session, county, dedupe="6fb-err")
    lead = _lead(session, case)
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone(case_id=case.id),),
        status=SkipTraceStatus.ERROR,
        error_code="lookup_failed",
        requires_human_review=False,
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert provider.calls == 1
    assert result.block_reason is SkipTraceWorkflowBlockReason.PROVIDER_NOT_SUCCESS
    assert result.provider_error_code == "lookup_failed"
    assert result.materialization_attempt_count == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_provider_not_found_skips_materialization(session: Session) -> None:
    county = _county(session, slug="harford-6fb-nf")
    case = _case(session, county, dedupe="6fb-nf")
    lead = _lead(session, case)
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(),
        status=SkipTraceStatus.NOT_FOUND,
        requires_human_review=False,
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.block_reason is SkipTraceWorkflowBlockReason.PROVIDER_NOT_SUCCESS
    assert result.materialization_attempt_count == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_provider_level_human_review_blocks_all_candidates(session: Session) -> None:
    county = _county(session, slug="harford-6fb-phr")
    case = _case(session, county, dedupe="6fb-phr")
    lead = _lead(session, case)
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone(case_id=case.id),),
        requires_human_review=True,
    )
    result = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert result.block_reason is (
        SkipTraceWorkflowBlockReason.PROVIDER_HUMAN_REVIEW_REQUIRED
    )
    assert result.materialization_attempt_count == 0
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_workflow_idempotent_second_run(session: Session) -> None:
    county = _county(session, slug="harford-6fb-idem")
    case = _case(session, county, dedupe="6fb-idem")
    lead = _lead(session, case)
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone(case_id=case.id), _email(case_id=case.id)),
    )
    first = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    second = run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    assert first.created_count == 2
    assert second.created_count == 0
    assert second.already_present_count == 2
    assert provider.calls == 2
    assert session.scalar(select(func.count()).select_from(Contact)) == 2
    assert (session.scalar(select(func.count()).select_from(Lead)) or 0) == 1


def test_no_owner_or_compliance_mutation(session: Session) -> None:
    county = _county(session, slug="harford-6fb-own")
    case = _case(session, county, dedupe="6fb-own")
    lead = _lead(session, case)
    owner = session.scalar(select(Owner).where(Owner.surplus_case_id == case.id))
    assert owner is not None
    original = owner.raw_name
    compliance_before = (
        session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    )
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone(case_id=case.id),),
    )
    run_skip_trace_for_lead(session, lead_id=lead.id, provider=provider)
    session.refresh(owner)
    assert owner.raw_name == original
    assert (
        session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    ) == compliance_before
