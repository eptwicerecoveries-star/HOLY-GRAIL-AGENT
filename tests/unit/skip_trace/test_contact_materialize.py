"""Phase 6F: Lead-gated Contact materialization and offline skip-trace foundation."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from surplus_ai.skip_trace.materialize import (
    MaterializeBlockReason,
    materialize_contact_candidate,
)
from surplus_ai.skip_trace.models import ContactCandidate, ContactCandidateType, SkipTraceStatus
from surplus_ai.skip_trace.normalize import normalize_email, normalize_phone
from surplus_ai.skip_trace.providers.credentials_missing import CredentialsMissingSkipTraceProvider
from surplus_ai.skip_trace.providers.fake import FakeSkipTraceProvider
from surplus_ai.skip_trace.providers.manual import ManualSkipTraceProvider
from surplus_ai.skip_trace.providers.null import NullSkipTraceProvider


@pytest.fixture
def sample_county(session: Session) -> County:
    county = County(
        slug="harford-6f",
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


@pytest.fixture
def sample_case(session: Session, sample_county: County) -> SurplusCase:
    case = SurplusCase(
        county_id=sample_county.id,
        parcel_id="01-234567",
        property_address_raw="123 MAIN ST",
        sale_date=date(2024, 3, 1),
        surplus_amount=Decimal("5000.00"),
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash="phase6f-test-hash-001",
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


@pytest.fixture
def sample_lead(session: Session, sample_case: SurplusCase) -> Lead:
    owner = session.scalar(select(Owner).where(Owner.surplus_case_id == sample_case.id))
    lead = Lead(
        surplus_case_id=sample_case.id,
        owner_id=owner.id if owner else None,
        status=LeadStatus.QUALIFIED,
    )
    session.add(lead)
    session.flush()
    return lead


def _phone_candidate(
    *,
    raw: str = "555-555-0100",
    case_id: uuid.UUID | None = None,
    requires_human_review: bool = False,
    provider_id: str = "fake_skip_trace",
) -> ContactCandidate:
    return ContactCandidate(
        provider_id=provider_id,
        candidate_type=ContactCandidateType.PHONE,
        raw_value=raw,
        surplus_case_id=case_id,
        confidence=0.9,
        requires_human_review=requires_human_review,
    )


def _email_candidate(
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


def test_normalize_phone_nanp() -> None:
    assert normalize_phone("555-555-0100") == "+15555550100"
    assert normalize_phone("1 (555) 555-0100") == "+15555550100"
    assert normalize_phone("") is None
    assert normalize_phone("   ") is None
    assert normalize_phone("12345") is None


def test_normalize_email_basic() -> None:
    assert normalize_email("  Person@Example.COM ") == "person@example.com"
    assert normalize_email("not-an-email") is None
    assert normalize_email("") is None
    assert normalize_email("a@b") is None


def test_valid_lead_materializes_phone(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    result = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(case_id=sample_case.id),
    )
    assert result.blocked is False
    assert result.created is True
    assert result.contact_id is not None
    contact = session.get(Contact, result.contact_id)
    assert contact is not None
    assert contact.lead_id == sample_lead.id
    assert contact.contact_type is ContactType.PHONE
    assert contact.value == "+15555550100"
    assert contact.source == "fake_skip_trace"
    assert contact.is_verified is False


def test_valid_lead_materializes_email(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    result = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_email_candidate(case_id=sample_case.id),
    )
    assert result.created is True
    contact = session.get(Contact, result.contact_id)
    assert contact is not None
    assert contact.contact_type is ContactType.EMAIL
    assert contact.value == "person@example.com"


def test_missing_lead_blocks(session: Session, sample_case: SurplusCase) -> None:
    result = materialize_contact_candidate(
        session,
        lead_id=uuid.uuid4(),
        candidate=_phone_candidate(case_id=sample_case.id),
    )
    assert result.block_reason is MaterializeBlockReason.LEAD_NOT_FOUND
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_case_mismatch_blocks(
    session: Session, sample_lead: Lead, sample_county: County
) -> None:
    other = SurplusCase(
        county_id=sample_county.id,
        parcel_id="99-999",
        property_address_raw="9 OTHER",
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash="phase6f-other-case",
    )
    session.add(other)
    session.flush()
    result = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(case_id=other.id),
    )
    assert result.block_reason is MaterializeBlockReason.LEAD_CASE_MISMATCH
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_no_implicit_lead_creation(session: Session, sample_case: SurplusCase) -> None:
    leads_before = session.scalar(select(func.count()).select_from(Lead)) or 0
    materialize_contact_candidate(
        session,
        lead_id=uuid.uuid4(),
        candidate=_phone_candidate(case_id=sample_case.id),
    )
    assert (session.scalar(select(func.count()).select_from(Lead)) or 0) == leads_before


def test_human_review_required_blocks(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    result = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(case_id=sample_case.id, requires_human_review=True),
    )
    assert result.block_reason is MaterializeBlockReason.HUMAN_REVIEW_REQUIRED
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_malformed_phone_blocks(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    result = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(raw="not-a-phone", case_id=sample_case.id),
    )
    assert result.block_reason is MaterializeBlockReason.INVALID_CONTACT


def test_empty_and_whitespace_phone_blocks(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    for raw in ("", "   "):
        result = materialize_contact_candidate(
            session,
            lead_id=sample_lead.id,
            candidate=_phone_candidate(raw=raw, case_id=sample_case.id),
        )
        assert result.block_reason is MaterializeBlockReason.INVALID_CONTACT


def test_malformed_email_blocks(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    result = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_email_candidate(raw="bad@", case_id=sample_case.id),
    )
    assert result.block_reason is MaterializeBlockReason.INVALID_CONTACT


def test_idempotent_duplicate_returns_existing(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    first = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(case_id=sample_case.id),
    )
    second = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(raw="(555) 555-0100", case_id=sample_case.id),
    )
    assert first.created is True
    assert second.created is False
    assert second.already_present is True
    assert second.contact_id == first.contact_id
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_db_unique_rejects_duplicate_when_precheck_bypassed(
    session: Session, sample_lead: Lead
) -> None:
    session.add(
        Contact(
            lead_id=sample_lead.id,
            contact_type=ContactType.PHONE,
            value="+15555550100",
            source="seed",
            is_verified=False,
        )
    )
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(
                Contact(
                    lead_id=sample_lead.id,
                    contact_type=ContactType.PHONE,
                    value="+15555550100",
                    source="bypass",
                    is_verified=False,
                )
            )
            session.flush()
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_same_value_allowed_on_different_leads(
    session: Session,
    sample_lead: Lead,
    sample_case: SurplusCase,
    sample_county: County,
) -> None:
    other_case = SurplusCase(
        county_id=sample_county.id,
        parcel_id="88-888",
        property_address_raw="8 OTHER",
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash="phase6f-lead2-case",
    )
    session.add(other_case)
    session.flush()
    other_lead = Lead(surplus_case_id=other_case.id, status=LeadStatus.QUALIFIED)
    session.add(other_lead)
    session.flush()
    materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(raw="5555550100", case_id=sample_case.id),
    )
    materialize_contact_candidate(
        session,
        lead_id=other_lead.id,
        candidate=_phone_candidate(raw="5555550100", case_id=other_case.id),
    )
    assert session.scalar(select(func.count()).select_from(Contact)) == 2


def test_race_recovery_returns_existing_on_integrity_error(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    existing = Contact(
        lead_id=sample_lead.id,
        contact_type=ContactType.PHONE,
        value="+15555550100",
        source="winner",
        is_verified=False,
    )
    session.add(existing)
    session.flush()
    existing_id = existing.id

    import surplus_ai.skip_trace.materialize as materialize_mod

    original = materialize_mod._find_exact
    calls = {"n": 0}

    def hide_once(
        sess: Session,
        lead_id: uuid.UUID,
        contact_type: ContactType,
        normalized: str,
    ) -> Contact | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original(sess, lead_id, contact_type, normalized)

    materialize_mod._find_exact = hide_once  # type: ignore[assignment]
    try:
        result = materialize_contact_candidate(
            session,
            lead_id=sample_lead.id,
            candidate=_phone_candidate(raw="5555550100", case_id=sample_case.id),
        )
    finally:
        materialize_mod._find_exact = original  # type: ignore[assignment]

    assert result.created is False
    assert result.already_present is True
    assert result.contact_id == existing_id
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_integrity_error_without_matching_contact_propagates(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    import surplus_ai.skip_trace.materialize as materialize_mod

    original_find = materialize_mod._find_exact

    def never_find(*_a: object, **_k: object) -> None:
        return None

    class BoomSession:
        def __init__(self, real: Session) -> None:
            self._real = real

        def get(self, *a: object, **k: object) -> object:
            return self._real.get(*a, **k)

        def begin_nested(self) -> object:
            class _Ctx:
                def __enter__(self_inner: object) -> None:
                    return None

                def __exit__(self_inner: object, *exc: object) -> bool:
                    return False

            return _Ctx()

        def add(self, _row: object) -> None:
            return None

        def flush(self) -> None:
            raise IntegrityError("stmt", {}, Exception("simulated"))

    materialize_mod._find_exact = never_find  # type: ignore[assignment]
    try:
        with pytest.raises(IntegrityError):
            materialize_contact_candidate(
                BoomSession(session),  # type: ignore[arg-type]
                lead_id=sample_lead.id,
                candidate=_phone_candidate(case_id=sample_case.id),
            )
    finally:
        materialize_mod._find_exact = original_find  # type: ignore[assignment]


def test_different_phone_creates_second_contact(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(raw="5555550100", case_id=sample_case.id),
    )
    materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(raw="5555550199", case_id=sample_case.id),
    )
    assert session.scalar(select(func.count()).select_from(Contact)) == 2


def test_does_not_overwrite_unrelated_contact(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    first = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(raw="5555550100", case_id=sample_case.id),
    )
    materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_email_candidate(case_id=sample_case.id),
    )
    contact = session.get(Contact, first.contact_id)
    assert contact is not None
    assert contact.value == "+15555550100"


def test_result_omits_sensitive_values(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    result = materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(raw="5555550100", case_id=sample_case.id),
    )
    blob = repr(result)
    assert "5555550100" not in blob
    assert "+15555550100" not in blob


def test_no_owner_compliance_mutation(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    owner = session.scalar(select(Owner).where(Owner.surplus_case_id == sample_case.id))
    assert owner is not None
    original = owner.raw_name
    compliance_before = (
        session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    )
    materialize_contact_candidate(
        session,
        lead_id=sample_lead.id,
        candidate=_phone_candidate(case_id=sample_case.id),
    )
    session.refresh(owner)
    assert owner.raw_name == original
    assert (
        session.scalar(select(func.count()).select_from(ComplianceEvaluation)) or 0
    ) == compliance_before


def test_fake_provider_returns_candidates_without_creating_contact(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone_candidate(case_id=sample_case.id),),
    )
    lookup = provider.lookup_contact_candidates(sample_lead)
    assert lookup.status is SkipTraceStatus.SUCCESS
    assert len(lookup.candidates) == 1
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_provider_then_materialize_path(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(_phone_candidate(case_id=sample_case.id),),
    )
    lookup = provider.lookup_contact_candidates(sample_lead)
    assert lookup.lead_id == sample_lead.id
    results = [
        materialize_contact_candidate(session, lead_id=sample_lead.id, candidate=c)
        for c in lookup.candidates
    ]
    assert results[0].created is True
    assert session.scalar(select(func.count()).select_from(Contact)) == 1


def test_null_manual_credentials_providers_create_no_contact(
    session: Session, sample_lead: Lead
) -> None:
    for provider in (
        NullSkipTraceProvider(),
        ManualSkipTraceProvider(),
        CredentialsMissingSkipTraceProvider("example_vendor", "SKIP_TRACE_API_KEY"),
    ):
        lookup = provider.lookup_contact_candidates(sample_lead)
        assert lookup.candidates == ()
        assert lookup.status in {
            SkipTraceStatus.SKIPPED,
            SkipTraceStatus.NOT_FOUND,
            SkipTraceStatus.ERROR,
        }
    assert session.scalar(select(func.count()).select_from(Contact)) == 0


def test_ambiguous_requires_review_does_not_materialize(
    session: Session, sample_lead: Lead, sample_case: SurplusCase
) -> None:
    provider = FakeSkipTraceProvider(
        "fake_skip_trace",
        candidates=(
            _phone_candidate(case_id=sample_case.id, requires_human_review=True),
        ),
        requires_human_review=True,
    )
    lookup = provider.lookup_contact_candidates(sample_lead)
    for c in lookup.candidates:
        result = materialize_contact_candidate(
            session, lead_id=sample_lead.id, candidate=c
        )
        assert result.block_reason is MaterializeBlockReason.HUMAN_REVIEW_REQUIRED
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
