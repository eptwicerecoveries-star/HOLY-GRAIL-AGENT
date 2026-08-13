from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    ClassificationMethod,
    CountySourceType,
    OwnerType,
    PublishingFrequency,
    SurplusCaseStatus,
    SurplusSourceType,
)
from surplus_ai.database.models.owner import Owner
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.models import PropertyLookupQuery, ProviderOutcome
from surplus_ai.research.persistence import parse_outcome_dict
from surplus_ai.research.providers.base import AbstractPropertyRecordProvider

FIXTURES_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "research" / "provider_outcomes.json"
)


@pytest.fixture(scope="module")
def outcome_fixtures() -> dict[str, Any]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def sample_county(session: Session) -> County:
    county = County(
        slug="harford",
        name="Harford County",
        state="MD",
        fips_code="24025",
        source_type=CountySourceType.MANUAL_UPLOAD,
        source_url=None,
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
        case_number=None,
        parcel_id="01-234567",
        property_address_raw="123 MAIN ST",
        sale_date=date(2024, 3, 1),
        surplus_amount=Decimal("5000.00"),
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        surplus_source_column="Surplus",
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash="research-test-hash-001",
    )
    session.add(case)
    session.flush()
    owner = Owner(
        surplus_case_id=case.id,
        raw_name="STEFFEN, DEBORAH",
        owner_type=OwnerType.INDIVIDUAL,
        first_name="DEBORAH",
        last_name="STEFFEN",
        classification_confidence=0.8,
        classification_method=ClassificationMethod.RULE,
    )
    session.add(owner)
    session.flush()
    session.refresh(case)
    return case


@pytest.fixture
def sample_query(sample_case: SurplusCase, sample_county: County) -> PropertyLookupQuery:
    return PropertyLookupQuery(
        state=sample_county.state,
        county_slug=sample_county.slug,
        parcel_id=sample_case.parcel_id,
        owner_raw_name="STEFFEN, DEBORAH",
        property_address_raw=sample_case.property_address_raw,
        sale_date="2024-03-01",
        owner_type="individual",
        surplus_case_id=sample_case.id,
    )


class FixtureProvider(AbstractPropertyRecordProvider):
    """Deterministic in-process provider for tests. Performs no network I/O."""

    def __init__(self, name: str, outcome: ProviderOutcome) -> None:
        self.name = name
        self._outcome = outcome

    def supports(self, state: str, county_slug: str) -> bool:
        return True

    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        return self._outcome


def fixture_outcome(fixtures: dict[str, Any], key: str) -> ProviderOutcome:
    return parse_outcome_dict(fixtures[key])
