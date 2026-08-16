"""Offline shipped-config tests for the disabled Lake County ArcGIS candidate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from surplus_ai.research.models import PropertyLookupQuery
from surplus_ai.research.providers.arcgis import (
    ArcGISFeatureServerProvider,
    ArcGISIdentityValueType,
)
from surplus_ai.research.registry import (
    COUNTIES_CONFIG_DIR,
    DEFAULT_PROVIDERS_PATH,
    ProviderRegistry,
)
from tests.unit.research.test_socrata import RecordingHttp, _query, http_json

_PROVIDER_ID = "lake_county_fl_pa_tax_parcels"
_LAYER_URL = (
    "https://gis.lakecountyfl.gov/lakegis/rest/services/"
    "OpenData/OpenData1/FeatureServer/12"
)
_MINIMUM_FIELDS = ("AltKey", "OwnerName", "PropertyAddress", "OBJECTID")


def _shipped() -> ProviderRegistry:
    return ProviderRegistry()


def _lake() -> ArcGISFeatureServerProvider:
    provider = _shipped().resolve(_PROVIDER_ID)
    assert isinstance(provider, ArcGISFeatureServerProvider)
    return provider


def _raw_lake_block() -> dict[str, Any]:
    raw: Any = yaml.safe_load(DEFAULT_PROVIDERS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    providers = raw["providers"]
    assert isinstance(providers, dict)
    block = providers[_PROVIDER_ID]
    assert isinstance(block, dict)
    return block


def test_shipped_providers_yaml_loads_lake_county_arcgis() -> None:
    registry = _shipped()
    assert _PROVIDER_ID in registry.registered_names()
    assert registry.provider_type(_PROVIDER_ID) == "arcgis"
    assert registry.default_provider_name == "manual_lookup"


def test_lake_county_options_are_disabled_minimum_text_identity() -> None:
    options = _lake()._options
    assert options.domain == "gis.lakecountyfl.gov"
    assert options.service_path == (
        "/lakegis/rest/services/OpenData/OpenData1/FeatureServer"
    )
    assert options.layer_id == 12
    assert options.parcel_field == "AltKey"
    assert options.parcel_value_type is ArcGISIdentityValueType.TEXT
    assert options.owner_field == "OwnerName"
    assert options.situs_address_field == "PropertyAddress"
    assert options.record_id_field == "OBJECTID"
    assert options.select_fields == _MINIMUM_FIELDS
    assert "ParcelNumber" not in options.select_fields
    assert options.query_limit == 5
    assert options.source_organization == "Lake County Property Appraiser"
    assert options.verified_for_automated_access is False
    assert options.access_reviewed_on is None
    assert options.account_field is None
    assert options.mailing_address_field is None
    dumped = options.model_dump()
    assert "access_reviewed_on" in dumped
    assert dumped["access_reviewed_on"] is None
    assert "token" not in dumped
    assert "credential" not in dumped
    assert "optional_credential" not in dumped


def test_lake_county_yaml_omits_review_credential_and_extra_fields() -> None:
    block = _raw_lake_block()
    options = block["options"]
    assert isinstance(options, dict)
    assert block.get("verified_for_automated_access") is None
    assert "access_reviewed_on" not in block
    assert "access_reviewed_on" not in options
    assert "requires_credential" not in block
    assert "token" not in options
    assert "credential" not in options
    assert "optional_credential" not in options
    assert "account_field" not in options
    assert "mailing_address_field" not in options
    assert "ParcelNumber" not in options["select_fields"]
    assert options["verified_for_automated_access"] is False
    assert options["select_fields"] == list(_MINIMUM_FIELDS)


def test_lake_county_reliability_override_is_conservative() -> None:
    policy = _shipped().reliability_for(_PROVIDER_ID)
    assert policy.cache.ttl_seconds == 0
    assert policy.retry.max_attempts == 1
    assert policy.retry.backoff_seconds == 0
    assert policy.rate_limit.per_second == 1


def test_no_county_selects_lake_county_arcgis() -> None:
    root = COUNTIES_CONFIG_DIR
    selected: list[str] = []
    for path in sorted(root.rglob("*.yaml")):
        if path.name.startswith("_"):
            continue
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        block = raw.get("research")
        if isinstance(block, dict) and block.get("property_provider") == _PROVIDER_ID:
            selected.append(str(path.relative_to(root)))
    assert selected == []
    marion = Path(root / "in" / "marion.yaml")
    assert marion.is_file()
    marion_raw = yaml.safe_load(marion.read_text(encoding="utf-8")) or {}
    assert "research" not in marion_raw


def test_listing_and_resolving_unverified_lake_makes_no_http() -> None:
    http = RecordingHttp(http_json([]))
    registry = ProviderRegistry(http_client=http)
    rows = registry.describe()
    names = [row["name"] for row in rows]
    assert _PROVIDER_ID in names
    lake = next(row for row in rows if row["name"] == _PROVIDER_ID)
    assert lake["type"] == "arcgis"
    assert lake["is_default"] == ""
    assert "not yet approved for automated access" in lake["description"]
    provider = registry.resolve(_PROVIDER_ID)
    assert isinstance(provider, ArcGISFeatureServerProvider)
    assert provider._options.verified_for_automated_access is False
    assert http.calls == []


def test_unverified_lake_lookup_fails_closed_before_http() -> None:
    http = RecordingHttp(http_json([]))
    registry = ProviderRegistry(http_client=http)
    provider = registry.resolve(_PROVIDER_ID)
    outcome = provider.lookup(_query(parcel_id="2866713"))
    assert http.calls == []
    assert outcome.error_code == "automated_access_not_verified"
    assert outcome.found is False
    assert outcome.retryable is False
    assert outcome.cacheable is False
    assert outcome.requires_human_review is True
    assert outcome.source_url == _LAYER_URL


def test_unverified_lake_lookup_rejects_owner_only_without_http() -> None:
    http = RecordingHttp(http_json([]))
    registry = ProviderRegistry(http_client=http)
    provider = registry.resolve(_PROVIDER_ID)
    outcome = provider.lookup(
        PropertyLookupQuery.model_validate(
            {
                "state": "FL",
                "county_slug": "lake",
                "owner_raw_name": "LAKE COUNTY",
            }
        )
    )
    assert http.calls == []
    assert outcome.error_code == "automated_access_not_verified"


def test_other_disabled_candidates_remain_unverified() -> None:
    registry = _shipped()
    assert registry.default_provider_name == "manual_lookup"
    franklin = registry.resolve("franklin_county_oh_auditor_parcels")
    pluto = registry.resolve("nyc_dcp_pluto")
    example = registry.resolve("example_arcgis")
    assert franklin._options.verified_for_automated_access is False
    assert franklin._options.access_reviewed_on is None
    assert pluto._options.verified_for_automated_access is False
    assert example._options.verified_for_automated_access is False
    assert example._options.access_reviewed_on is None
