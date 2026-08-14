from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
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
from surplus_ai.research.registry import ProviderRegistry

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
        self.calls = 0

    def supports(self, state: str, county_slug: str) -> bool:
        return True

    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        self.calls += 1
        return self._outcome


class SequenceProvider(AbstractPropertyRecordProvider):
    """Returns a sequence of outcomes; last outcome repeats if exhausted."""

    def __init__(self, name: str, outcomes: list[ProviderOutcome]) -> None:
        self.name = name
        self._outcomes = outcomes
        self.calls = 0

    def supports(self, state: str, county_slug: str) -> bool:
        return True

    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        index = min(self.calls, len(self._outcomes) - 1)
        self.calls += 1
        return self._outcomes[index]


class FakeClock:
    """Advances only when sleep() is called. No real waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def wall(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=self.t)


def fixture_outcome(fixtures: dict[str, Any], key: str) -> ProviderOutcome:
    return parse_outcome_dict(fixtures[key])


def write_providers_yaml(
    tmp_path: Path,
    *,
    reliability: dict[str, Any] | None = None,
    providers: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    import yaml

    payload: dict[str, Any] = {
        "schema_version": 1,
        "default_property_provider": "manual_lookup",
        "providers": providers
        or {
            "manual_lookup": {"type": "manual", "description": "test"},
            "null": {"type": "null", "description": "test"},
        },
    }
    if reliability is not None:
        payload["reliability"] = reliability
    if extra:
        payload.update(extra)
    path = tmp_path / "providers.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def registry_from_yaml(tmp_path: Path, **kwargs: Any) -> ProviderRegistry:
    path = write_providers_yaml(tmp_path, **kwargs)
    return ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def force_provider(registry: ProviderRegistry, name: str) -> None:
    registry.resolve_for_county = lambda _s, _c: registry.resolve(name)  # type: ignore[method-assign]


SHIPPED_RELIABILITY: dict[str, Any] = {
    "cache": {"ttl_seconds": 86400},
    "retry": {"max_attempts": 3, "backoff_seconds": 0.5},
    "rate_limit": {"per_second": 0},
}
