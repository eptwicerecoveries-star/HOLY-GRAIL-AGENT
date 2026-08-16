"""Offline shipped-config tests for the disabled NYC PLUTO Socrata candidate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from surplus_ai.research.models import PropertyLookupQuery
from surplus_ai.research.providers.socrata import (
    SocrataIdentityValueType,
    SocrataOpenDataProvider,
)
from surplus_ai.research.registry import COUNTIES_CONFIG_DIR, ProviderRegistry
from tests.unit.research.test_socrata import RecordingHttp, _query, http_json


def _shipped() -> ProviderRegistry:
    return ProviderRegistry()


def _pluto() -> SocrataOpenDataProvider:
    provider = _shipped().resolve("nyc_dcp_pluto")
    assert isinstance(provider, SocrataOpenDataProvider)
    return provider


def test_shipped_providers_yaml_loads_nyc_dcp_pluto() -> None:
    registry = _shipped()
    assert "nyc_dcp_pluto" in registry.registered_names()
    assert registry.provider_type("nyc_dcp_pluto") == "socrata"
    assert registry.default_provider_name == "manual_lookup"


def test_nyc_dcp_pluto_options_are_disabled_number_identity() -> None:
    options = _pluto()._options
    assert options.domain == "data.cityofnewyork.us"
    assert options.dataset_id == "64uk-42ks"
    assert options.parcel_field == "bbl"
    assert options.parcel_value_type is SocrataIdentityValueType.NUMBER
    assert options.owner_field == "ownername"
    assert options.situs_address_field == "address"
    assert options.query_limit == 5
    assert options.select_fields == ("bbl", "ownername", "address")
    assert options.source_organization == "New York City Department of City Planning"
    assert options.verified_for_automated_access is False
    assert options.access_reviewed_on is None
    assert options.account_field is None
    assert options.mailing_address_field is None
    assert options.record_id_field is None
    assert options.optional_credential is None
    dumped = options.model_dump()
    assert "access_reviewed_on" in dumped
    assert dumped["access_reviewed_on"] is None


def test_nyc_dcp_pluto_reliability_override_is_conservative() -> None:
    policy = _shipped().reliability_for("nyc_dcp_pluto")
    assert policy.cache.ttl_seconds == 0
    assert policy.retry.max_attempts == 1
    assert policy.retry.backoff_seconds == 0
    assert policy.rate_limit.per_second == 1


def test_no_county_selects_nyc_dcp_pluto() -> None:
    root = COUNTIES_CONFIG_DIR
    selected: list[str] = []
    for path in sorted(root.rglob("*.yaml")):
        if path.name.startswith("_"):
            continue
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        block = raw.get("research")
        if isinstance(block, dict) and block.get("property_provider") == "nyc_dcp_pluto":
            selected.append(str(path.relative_to(root)))
    assert selected == []
    marion = Path(root / "in" / "marion.yaml")
    assert marion.is_file()
    marion_raw = yaml.safe_load(marion.read_text(encoding="utf-8")) or {}
    assert "research" not in marion_raw


def test_listing_and_resolving_unverified_pluto_makes_no_http() -> None:
    http = RecordingHttp(http_json([]))
    registry = ProviderRegistry(http_client=http)
    rows = registry.describe()
    names = [row["name"] for row in rows]
    assert "nyc_dcp_pluto" in names
    pluto = next(row for row in rows if row["name"] == "nyc_dcp_pluto")
    assert pluto["type"] == "socrata"
    assert pluto["is_default"] == ""
    assert "not yet approved for automated access" in pluto["description"]
    provider = registry.resolve("nyc_dcp_pluto")
    assert isinstance(provider, SocrataOpenDataProvider)
    assert provider._options.verified_for_automated_access is False
    assert http.calls == []


def test_unverified_pluto_lookup_fails_closed_before_http() -> None:
    http = RecordingHttp(http_json([]))
    registry = ProviderRegistry(http_client=http)
    provider = registry.resolve("nyc_dcp_pluto")
    outcome = provider.lookup(
        _query(parcel_id="123", owner_raw_name=None, property_address_raw=None)
    )
    assert http.calls == []
    assert outcome.error_code == "automated_access_not_verified"
    assert outcome.found is False
    assert outcome.retryable is False
    assert outcome.cacheable is False
    assert outcome.requires_human_review is True
    assert outcome.source_url == "https://data.cityofnewyork.us/resource/64uk-42ks.json"


def test_unverified_pluto_lookup_rejects_owner_only_without_http() -> None:
    http = RecordingHttp(http_json([]))
    registry = ProviderRegistry(http_client=http)
    provider = registry.resolve("nyc_dcp_pluto")
    outcome = provider.lookup(
        PropertyLookupQuery.model_validate(
            {
                "state": "NY",
                "county_slug": "new_york",
                "owner_raw_name": "CITY OF NEW YORK",
            }
        )
    )
    assert http.calls == []
    assert outcome.error_code == "automated_access_not_verified"
