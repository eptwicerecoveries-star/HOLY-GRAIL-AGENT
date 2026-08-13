from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    CountySourceType,
    PublishingFrequency,
    SurplusCaseStatus,
    SurplusSourceType,
)
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.candidate import CandidateSelector
from surplus_ai.research.exceptions import CandidateSelectionError


def test_selects_case_with_parcel(
    session: Session, sample_case: SurplusCase, sample_county: County
) -> None:
    candidate = CandidateSelector(session).get_case_candidate(sample_case.id)
    assert candidate.parcel_id == "01-234567"
    assert candidate.state == sample_county.state
    assert candidate.selection_reason == "parcel_id"


def test_rejects_case_without_identity(session: Session, sample_county: County) -> None:
    case = SurplusCase(
        county_id=sample_county.id,
        parcel_id=None,
        property_address_raw=None,
        case_number=None,
        surplus_amount=Decimal("1.00"),
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash="no-identity",
        sale_date=date(2024, 1, 1),
    )
    session.add(case)
    session.flush()
    with pytest.raises(CandidateSelectionError):
        CandidateSelector(session).get_case_candidate(case.id)


def test_pending_filters_by_state(
    session: Session, sample_case: SurplusCase, sample_county: County
) -> None:
    other = County(
        slug="marion",
        name="Marion County",
        state="IN",
        source_type=CountySourceType.MANUAL_UPLOAD,
        parsing_profile_key="in-marion",
        compliance_state_ref="IN",
        publishing_frequency=PublishingFrequency.IRREGULAR,
        is_active=True,
    )
    session.add(other)
    session.flush()
    session.add(
        SurplusCase(
            county_id=other.id,
            parcel_id="99",
            surplus_amount=Decimal("2.00"),
            surplus_is_explicit=True,
            surplus_source=SurplusSourceType.EXPLICIT,
            status=SurplusCaseStatus.NORMALIZED,
            dedupe_hash="in-case",
        )
    )
    session.flush()

    md = CandidateSelector(session).pending(state="MD", limit=50)
    assert all(c.state == "MD" for c in md)
    assert any(c.surplus_case_id == sample_case.id for c in md)


def test_missing_case_raises(session: Session) -> None:
    with pytest.raises(CandidateSelectionError):
        CandidateSelector(session).get_case_candidate(uuid.uuid4())
