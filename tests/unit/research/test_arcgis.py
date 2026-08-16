from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from surplus_ai.research.exceptions import ResearchConfigError
from surplus_ai.research.http import HttpGetResult, ResearchHttpClient
from surplus_ai.research.models import ProviderOutcomeStatus
from surplus_ai.research.providers.arcgis import (
    ArcGISFeatureServerProvider,
    ArcGISIdentityValueType,
    ArcGISProviderOptions,
)
from surplus_ai.research.providers.socrata import SocrataOpenDataProvider
from surplus_ai.research.registry import COUNTIES_CONFIG_DIR, ProviderRegistry, ProviderSpec
from tests.unit.research.conftest import write_providers_yaml
from tests.unit.research.test_socrata import (
    RecordingHttp,
    _query,
)
from tests.unit.research.test_socrata import (
    valid_options as socrata_valid_options,
)

QUERY_URL = (
    "https://gis.example.gov/arcgis/rest/services/Example/Parcels/FeatureServer/0/query"
)
LAYER_URL = "https://gis.example.gov/arcgis/rest/services/Example/Parcels/FeatureServer/0"
_SERVICE_PATH = "/arcgis/rest/services/Example/Parcels/FeatureServer"


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def valid_options(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "domain": "gis.example.gov",
        "service_path": _SERVICE_PATH,
        "layer_id": 0,
        "parcel_field": "PARCELID",
        "owner_field": "OWNER_NAME",
        "situs_address_field": "SITEADDRESS",
        "record_id_field": "OBJECTID",
        "select_fields": ["PARCELID", "OWNER_NAME", "SITEADDRESS", "OBJECTID"],
        "query_limit": 5,
        "source_organization": "Example County GIS",
        "verified_for_automated_access": True,
        "access_reviewed_on": "2026-08-01",
    }
    payload.update(overrides)
    return payload


def options(**overrides: Any) -> ArcGISProviderOptions:
    return ArcGISProviderOptions.model_validate(valid_options(**overrides))


def _provider(
    http: RecordingHttp | ResearchHttpClient,
    **option_overrides: Any,
) -> ArcGISFeatureServerProvider:
    return ArcGISFeatureServerProvider(
        "example_arcgis",
        options(**option_overrides),
        http=http,
        now=_now,
    )


def _attrs(**overrides: Any) -> dict[str, Any]:
    row = {
        "PARCELID": "ABC123",
        "OWNER_NAME": "STEFFEN, DEBORAH",
        "SITEADDRESS": "123 MAIN ST",
        "OBJECTID": 1,
    }
    row.update(overrides)
    return row


def _feature(attributes: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"attributes": _attrs() if attributes is None else attributes}
    item.update(extra)
    return item


def _envelope(*features: dict[str, Any], **top: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"features": list(features)}
    payload.update(top)
    return payload


def http_json(payload: object, status: int = 200) -> HttpGetResult:
    return HttpGetResult(status_code=status, body=json.dumps(payload).encode("utf-8"))


def _assert_incomplete(outcome: Any, http: RecordingHttp) -> None:
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.found is False
    assert outcome.error_code == "result_incomplete"
    assert outcome.cacheable is False
    assert outcome.retryable is False
    assert outcome.requires_human_review is True
    assert outcome.evidence == ()
    assert len(http.calls) == 1
    assert "resultOffset" not in http.calls[0]["params"]
    assert http.calls[0]["url"] == QUERY_URL


def test_arcgis_config_valid() -> None:
    parsed = options()
    assert parsed.domain == "gis.example.gov"
    assert parsed.service_path == _SERVICE_PATH
    assert parsed.layer_id == 0
    assert parsed.parcel_value_type is ArcGISIdentityValueType.TEXT
    assert parsed.account_value_type is ArcGISIdentityValueType.TEXT
    assert parsed.verified_for_automated_access is True
    dumped = parsed.model_dump()
    assert "token" not in dumped
    assert "optional_credential" not in dumped
    assert "url" not in dumped
    assert "where" not in dumped


def test_unknown_arcgis_option_rejected() -> None:
    for extra in (
        {"token": "secret"},
        {"optional_credential": "SURPLUS_AI_ARCGIS_TOKEN"},
        {"url": "https://gis.example.gov/arcgis/rest/services/Example/Parcels/FeatureServer"},
        {"where": "1=1"},
        {"map_server": True},
    ):
        with pytest.raises(ValidationError):
            ArcGISProviderOptions.model_validate(valid_options(**extra))


@pytest.mark.parametrize(
    "domain",
    [
        "https://gis.example.gov",
        "gis.example.gov/arcgis",
        "8.8.8.8",
        "127.0.0.1",
        "localhost",
        " gis.example.gov",
        "gis.example.gov ",
        "gis.example.gov:443",
        "user@gis.example.gov",
        "",
        "not a host",
    ],
)
def test_invalid_domain_rejected(domain: str) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(domain=domain)


@pytest.mark.parametrize(
    "service_path",
    [
        "arcgis/rest/services/Example/Parcels/FeatureServer",
        "/arcgis/rest/services/Example/Parcels/FeatureServer/",
        "/arcgis/services/Example/Parcels/FeatureServer",
        "/arcgis/rest/services/Example/Parcels/MapServer",
        "/arcgis/rest/services/Example/Parcels/FeatureServer?f=json",
        "/arcgis/rest/services/Example/Parcels/FeatureServer#layer",
        "/arcgis/rest/services/Example/Parcels%2FFeatureServer",
        "/arcgis/rest/services/Example/../Parcels/FeatureServer",
        "/arcgis/rest/services/./Parcels/FeatureServer",
        "/arcgis/rest/services/Example\\Parcels/FeatureServer",
        "/arcgis//rest/services/Example/Parcels/FeatureServer",
        "/arcgis/rest/services/Example@Parcels/FeatureServer",
        "/arcgis/rest/services/Example:Parcels/FeatureServer",
        "https://gis.example.gov/arcgis/rest/services/Example/Parcels/FeatureServer",
        "/arcgis/rest/services/Example/Parcels/FeatureServer/0",
        "",
    ],
)
def test_invalid_service_path_rejected(service_path: str) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(service_path=service_path)


def test_service_path_rejects_percent_traversal_and_mapserver() -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(service_path="/arcgis/rest/services/Example/Parcels/FeatureServer?x=1")
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(service_path="/arcgis/rest/services/Example/Parcels/MapServer")
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(service_path="/arcgis/rest/services/Example%2e%2e/Parcels/FeatureServer")


@pytest.mark.parametrize("layer_id", [-1, 10000, True, False, "0", 1.5, None])
def test_invalid_layer_id_rejected(layer_id: object) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(layer_id=layer_id)


def test_layer_id_bounds_accepted() -> None:
    assert options(layer_id=0).layer_id == 0
    assert options(layer_id=9999).layer_id == 9999


def test_query_limit_min_max() -> None:
    assert options(query_limit=1).query_limit == 1
    assert options(query_limit=100).query_limit == 100
    assert options().query_limit == 5
    payload = valid_options()
    del payload["query_limit"]
    assert ArcGISProviderOptions.model_validate(payload).query_limit == 10


@pytest.mark.parametrize("query_limit", [0, 101, True, False, 1.5, "10"])
def test_invalid_query_limit_rejected(query_limit: object) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(query_limit=query_limit)


def test_unverified_does_not_require_review_metadata() -> None:
    parsed = options(
        verified_for_automated_access=False,
        source_organization=None,
        access_reviewed_on=None,
    )
    assert parsed.verified_for_automated_access is False
    assert parsed.access_reviewed_on is None


def test_verified_true_requires_organization_and_date() -> None:
    with pytest.raises(ValidationError):
        ArcGISProviderOptions.model_validate(
            valid_options(source_organization=None)
        )
    with pytest.raises(ValidationError):
        ArcGISProviderOptions.model_validate(
            valid_options(access_reviewed_on=None)
        )


def test_unverified_provider_makes_no_http() -> None:
    http = RecordingHttp(http_json(_envelope()))
    outcome = _provider(http, verified_for_automated_access=False).lookup(_query())
    assert http.calls == []
    assert outcome.error_code == "automated_access_not_verified"
    assert outcome.found is False
    assert outcome.cacheable is False
    assert outcome.retryable is False
    assert outcome.requires_human_review is True


def test_existing_socrata_config_still_loads() -> None:
    registry = ProviderRegistry()
    assert registry.default_provider_name == "manual_lookup"
    assert "example_socrata" in registry.registered_names()
    assert "nyc_dcp_pluto" in registry.registered_names()
    assert registry.provider_type("example_socrata") == "socrata"
    assert registry.provider_type("nyc_dcp_pluto") == "socrata"
    socrata = registry.resolve("example_socrata")
    pluto = registry.resolve("nyc_dcp_pluto")
    assert isinstance(socrata, SocrataOpenDataProvider)
    assert isinstance(pluto, SocrataOpenDataProvider)
    assert socrata._options.verified_for_automated_access is False
    assert pluto._options.verified_for_automated_access is False


def test_shipped_example_arcgis_is_listable_and_unverified() -> None:
    registry = ProviderRegistry()
    assert "example_arcgis" in registry.registered_names()
    assert registry.provider_type("example_arcgis") == "arcgis"
    provider = registry.resolve("example_arcgis")
    assert isinstance(provider, ArcGISFeatureServerProvider)
    assert provider._options.verified_for_automated_access is False
    assert provider._options.access_reviewed_on is None
    assert provider._options.domain == "gis.example.gov"
    dumped = provider._options.model_dump()
    assert "token" not in dumped
    http = RecordingHttp(http_json(_envelope()))
    live = ArcGISFeatureServerProvider(
        "example_arcgis", provider._options, http=http, now=_now
    )
    outcome = live.lookup(_query(parcel_id="ABC123"))
    assert http.calls == []
    assert outcome.error_code == "automated_access_not_verified"


def test_manual_lookup_remains_default() -> None:
    registry = ProviderRegistry()
    assert registry.default_provider_name == "manual_lookup"


def test_no_county_selects_arcgis() -> None:
    root = COUNTIES_CONFIG_DIR
    selected: list[str] = []
    for path in sorted(root.rglob("*.yaml")):
        if path.name.startswith("_"):
            continue
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        block = raw.get("research")
        if not isinstance(block, dict):
            continue
        name = block.get("property_provider")
        if name == "example_arcgis" or (
            isinstance(name, str) and "arcgis" in name.lower()
        ):
            selected.append(str(path.relative_to(root)))
    assert selected == []


def test_no_official_arcgis_provider_configured() -> None:
    registry = ProviderRegistry()
    arcgis_names = [
        row["name"] for row in registry.describe() if row["type"] == "arcgis"
    ]
    assert arcgis_names == ["example_arcgis"]
    for row in registry.describe():
        if row["type"] == "arcgis":
            provider = registry.resolve(row["name"])
            assert isinstance(provider, ArcGISFeatureServerProvider)
            assert provider._options.verified_for_automated_access is False


def test_socrata_yaml_still_rejects_unknown_keys(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        providers={
            "manual_lookup": {"type": "manual"},
            "example_socrata": {
                "type": "socrata",
                "options": socrata_valid_options(allow_insecure_http=True),
            },
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


@pytest.mark.parametrize(
    "name",
    ["PARCELID", "Parcel_ID", "OBJECTID", "FIELD123"],
)
def test_safe_arcgis_field_names_accepted(name: str) -> None:
    parsed = options(parcel_field=name, select_fields=[name, "OWNER_NAME"])
    assert parsed.parcel_field == name
    assert name in parsed.select_fields


@pytest.mark.parametrize(
    "name",
    [
        "PARCELID OR 1=1",
        "PARCELID=1",
        "PARCELID;DROP",
        "PARCELID'",
        'PARCELID"',
        "PARCELID)",
        "PARCELID(",
        "PARCEL.ID",
        "PARCEL-ID",
        "PARCEL ID",
        "*",
        "foo,bar",
        "foo%20bar",
        "foo/bar",
        "foo\\bar",
        "foo?x",
        "foo#x",
    ],
)
def test_unsafe_arcgis_field_names_rejected(name: str) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(parcel_field=name)
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(select_fields=[name])
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(account_field=name)


def test_socrata_type_cannot_parse_arcgis_options() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate({"type": "socrata", "options": valid_options()})


def test_arcgis_type_cannot_parse_socrata_options() -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        ProviderSpec.model_validate(
            {"type": "arcgis", "options": socrata_valid_options()}
        )


def test_socrata_type_rejects_prebuilt_arcgis_options_instance() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate(
            {"type": "socrata", "options": options()}
        )


def test_arcgis_type_rejects_prebuilt_socrata_options_instance() -> None:
    from surplus_ai.research.providers.socrata import SocrataProviderOptions

    socrata = SocrataProviderOptions.model_validate(socrata_valid_options())
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate({"type": "arcgis", "options": socrata})


def test_manual_null_and_credentials_reject_options() -> None:
    for provider_type in ("manual", "null", "credentials_required"):
        with pytest.raises(ValidationError):
            ProviderSpec.model_validate(
                {"type": provider_type, "options": valid_options()}
            )


def test_arcgis_type_requires_options() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate({"type": "arcgis"})


def test_socrata_type_requires_options() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate({"type": "socrata"})


def test_registry_rejects_socrata_type_with_arcgis_keys(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        providers={
            "manual_lookup": {"type": "manual"},
            "wrong": {"type": "socrata", "options": valid_options()},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_registry_rejects_arcgis_type_with_socrata_keys(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        providers={
            "manual_lookup": {"type": "manual"},
            "wrong": {"type": "arcgis", "options": socrata_valid_options()},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_exact_query_shape_for_text_parcel() -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http).lookup(_query(parcel_id="ABC123"))
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"] == QUERY_URL
    params = call["params"]
    assert params["where"] == "PARCELID = 'ABC123'"
    assert params["outFields"] == "PARCELID,OWNER_NAME,SITEADDRESS,OBJECTID"
    assert params["returnGeometry"] == "false"
    assert params["f"] == "json"
    assert params["resultRecordCount"] == "5"
    assert params["outFields"] != "*"
    assert "token" not in params
    assert "resultOffset" not in params
    assert "geometry" not in params
    assert set(params) == {
        "where",
        "outFields",
        "returnGeometry",
        "f",
        "resultRecordCount",
    }
    assert call["headers"] == {}


def test_text_rhs_quoted_and_numeric_rhs_unquoted() -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http).lookup(_query(parcel_id="O'Brien"))
    assert http.calls[0]["params"]["where"] == "PARCELID = 'O''Brien'"
    http = RecordingHttp(http_json(_envelope()))
    _provider(http, parcel_value_type="number").lookup(_query(parcel_id="00123"))
    assert http.calls[0]["params"]["where"] == "PARCELID = 123"
    assert "'" not in http.calls[0]["params"]["where"]


def test_injection_string_remains_quoted_literal() -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http).lookup(_query(parcel_id="'; OR 1=1--"))
    assert http.calls[0]["params"]["where"] == "PARCELID = '''; OR 1=1--'"
    assert http.calls[0]["url"] == QUERY_URL


def test_no_caller_where_or_second_request() -> None:
    http = RecordingHttp(http_json(_envelope(_feature())))
    _provider(http).lookup(_query(parcel_id="ABC123"))
    assert len(http.calls) == 1
    assert "resultOffset" not in http.calls[0]["params"]
    assert http.calls[0]["params"]["where"].startswith("PARCELID = ")


def test_account_lookup_uses_configured_account_field() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _feature(
                    _attrs(PARCELID="other", ACCOUNTID="A-9", OBJECTID=2)
                )
            )
        )
    )
    outcome = _provider(http, account_field="ACCOUNTID").lookup(
        _query(parcel_id=None, account_id="A-9")
    )
    assert http.calls[0]["params"]["where"] == "ACCOUNTID = 'A-9'"
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "exact_account"


def test_account_lookup_without_account_field_is_missing_identity() -> None:
    http = RecordingHttp(http_json(_envelope()))
    outcome = _provider(http, account_field=None).lookup(
        _query(parcel_id=None, account_id="A-9")
    )
    assert http.calls == []
    assert outcome.error_code == "missing_identity"


def test_owner_only_lookup_is_not_allowed() -> None:
    http = RecordingHttp(http_json(_envelope()))
    outcome = _provider(http).lookup(
        _query(parcel_id=None, account_id=None, owner_raw_name="X")
    )
    assert http.calls == []
    assert outcome.error_code == "missing_identity"


def test_zero_features_is_not_found() -> None:
    http = RecordingHttp(http_json(_envelope()))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND
    assert outcome.found is False
    assert outcome.cacheable is True
    assert outcome.requires_human_review is False


def test_one_exact_parcel_is_success() -> None:
    http = RecordingHttp(http_json(_envelope(_feature())))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is False
    parcels = [a.original_value for a in outcome.evidence if a.field == "parcel_id"]
    assert parcels == ["ABC123"]
    assert outcome.source_url == LAYER_URL
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "exact_parcel"


def test_server_row_failing_local_match_is_ignored() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _feature(_attrs(PARCELID="OTHER", OBJECTID=2)),
            )
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND
    assert outcome.found is False
    assert len(http.calls) == 1


def test_one_match_plus_non_match_is_success() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _feature(_attrs(PARCELID="OTHER", OBJECTID=2)),
                _feature(),
            )
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is False


def test_multiple_complete_matches_require_review() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _feature(_attrs(OWNER_NAME="A", OBJECTID=1)),
                _feature(_attrs(OWNER_NAME="B", OBJECTID=2)),
            )
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is True
    assert outcome.cacheable is True
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "multiple"
    assert outcome.raw_response["record_ids"] == ["1", "2"]


def test_duplicate_exact_identities_require_review() -> None:
    http = RecordingHttp(http_json(_envelope(_feature(), _feature(_attrs(OBJECTID=2)))))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.requires_human_review is True
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "multiple"


def test_missing_features_is_malformed() -> None:
    http = RecordingHttp(http_json({"not_features": []}))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "malformed_provider_response"
    assert outcome.retryable is False
    assert outcome.cacheable is False
    assert outcome.requires_human_review is True


def test_features_not_list_is_malformed() -> None:
    http = RecordingHttp(http_json({"features": {"attributes": _attrs()}}))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "malformed_provider_response"


def test_feature_not_mapping_is_malformed() -> None:
    http = RecordingHttp(http_json({"features": ["nope"]}))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "malformed_provider_response"


def test_attributes_missing_is_malformed() -> None:
    http = RecordingHttp(http_json({"features": [{"geometry": {}}]}))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "malformed_provider_response"


def test_attributes_not_mapping_is_malformed() -> None:
    http = RecordingHttp(http_json({"features": [{"attributes": ["x"]}]}))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "malformed_provider_response"


def test_configured_required_field_missing_is_schema_mismatch() -> None:
    http = RecordingHttp(
        http_json(_envelope(_feature({"PARCELID": "ABC123", "OBJECTID": 1})))
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "schema_mismatch"
    assert outcome.retryable is False
    assert outcome.cacheable is False
    assert outcome.requires_human_review is True


def test_configured_field_non_scalar_is_schema_mismatch() -> None:
    http = RecordingHttp(
        http_json(_envelope(_feature(_attrs(OWNER_NAME={"n": "x"}))))
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "schema_mismatch"


def test_null_owner_and_address_supported() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(_feature(_attrs(OWNER_NAME=None, SITEADDRESS=None)))
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    owners = [a.original_value for a in outcome.evidence if a.field == "owner_name_on_record"]
    addresses = [a.original_value for a in outcome.evidence if a.field == "current_address"]
    assert owners == [None]
    assert addresses == [None]


def test_geometry_returned_is_ignored() -> None:
    geometry = {"rings": [[[0, 0], [1, 1], [0, 0]]]}
    http = RecordingHttp(
        http_json(_envelope(_feature(_attrs(), geometry=geometry)))
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    dumped = json.dumps(outcome.model_dump(mode="json"))
    assert "rings" not in dumped
    assert "geometry" not in dumped
    assert outcome.raw_response is not None
    assert "geometry" not in outcome.raw_response


def test_extra_attributes_are_not_mapped_or_persisted() -> None:
    http = RecordingHttp(
        http_json(_envelope(_feature(_attrs(UNRELATED="secret-extra", TAXYEAR=2024))))
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    fields = {a.field for a in outcome.evidence}
    assert "UNRELATED" not in fields
    dumped = json.dumps(outcome.raw_response)
    assert "secret-extra" not in dumped
    assert "TAXYEAR" not in dumped
    assert "UNRELATED" not in dumped


def test_malformed_json() -> None:
    http = RecordingHttp(HttpGetResult(status_code=200, body=b"{not-json"))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "malformed_provider_response"
    assert outcome.retryable is False
    assert outcome.requires_human_review is True


def test_top_level_arcgis_error_object() -> None:
    http = RecordingHttp(
        http_json(
            {
                "error": {
                    "code": 400,
                    "message": "Cannot perform query",
                    "details": ["bad where"],
                },
                "features": [_feature()],
            }
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.found is False
    assert outcome.error_code == "arcgis_error"
    assert outcome.retryable is False
    assert outcome.requires_human_review is True
    dumped = json.dumps(outcome.model_dump(mode="json"))
    assert "Cannot perform query" not in dumped
    assert "bad where" not in dumped
    assert outcome.raw_response is not None
    assert outcome.raw_response.get("arcgis_error_code") == 400


@pytest.mark.parametrize("code", [498, 499])
def test_arcgis_auth_error_codes_map_to_http_401(code: int) -> None:
    http = RecordingHttp(
        http_json(
            {
                "error": {
                    "code": code,
                    "message": "Token required",
                    "details": ["invalid token"],
                }
            }
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.error_code == "http_401"
    assert outcome.retryable is False
    assert outcome.requires_human_review is True
    dumped = json.dumps(outcome.model_dump(mode="json"))
    assert "Token required" not in dumped
    assert "invalid token" not in dumped
    assert outcome.raw_response is not None
    assert outcome.raw_response.get("arcgis_error_code") == code


def test_exceeded_transfer_limit_true_zero_local_matches() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _feature(_attrs(PARCELID="OTHER", OBJECTID=9)),
                exceededTransferLimit=True,
            )
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    _assert_incomplete(outcome, http)
    assert outcome.raw_response is not None
    assert outcome.raw_response["exceeded_transfer_limit"] is True


def test_exceeded_transfer_limit_true_one_local_match() -> None:
    http = RecordingHttp(
        http_json(_envelope(_feature(), exceededTransferLimit=True))
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    _assert_incomplete(outcome, http)
    assert outcome.status is not ProviderOutcomeStatus.SUCCESS
    assert outcome.status is not ProviderOutcomeStatus.NOT_FOUND


def test_exceeded_transfer_limit_true_multiple_local_matches() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _feature(_attrs(OBJECTID=1)),
                _feature(_attrs(OBJECTID=2)),
                exceededTransferLimit=True,
            )
        )
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    _assert_incomplete(outcome, http)
    assert outcome.evidence == ()


def test_exceeded_transfer_limit_false_one_local_match() -> None:
    http = RecordingHttp(
        http_json(_envelope(_feature(), exceededTransferLimit=False))
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert len(http.calls) == 1


def test_exceeded_transfer_limit_absent_one_local_match() -> None:
    http = RecordingHttp(http_json(_envelope(_feature())))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert len(http.calls) == 1
    assert "exceeded_transfer_limit" not in (outcome.raw_response or {})


@pytest.mark.parametrize("flag", ["true", 1, {}, []])
def test_malformed_transfer_flag(flag: object) -> None:
    http = RecordingHttp(
        http_json(_envelope(_feature(), exceededTransferLimit=flag))
    )
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.error_code == "malformed_provider_response"
    assert outcome.cacheable is False
    assert outcome.retryable is False
    assert outcome.requires_human_review is True
    assert outcome.found is False
    assert len(http.calls) == 1
    assert "resultOffset" not in http.calls[0]["params"]


def test_result_count_greater_than_query_limit_is_incomplete() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _feature(_attrs(OBJECTID=1)),
                _feature(_attrs(OBJECTID=2)),
            )
        )
    )
    outcome = _provider(http, query_limit=1).lookup(_query(parcel_id="ABC123"))
    _assert_incomplete(outcome, http)
    assert http.calls[0]["params"]["resultRecordCount"] == "1"
    assert outcome.raw_response is not None
    assert outcome.raw_response["result_count"] == 2


@pytest.mark.parametrize(
    ("result", "status", "code", "retryable"),
    [
        (
            HttpGetResult(status_code=400, body=b"{}"),
            ProviderOutcomeStatus.ERROR,
            "http_400",
            False,
        ),
        (
            HttpGetResult(status_code=401, body=b"{}"),
            ProviderOutcomeStatus.ERROR,
            "http_401",
            False,
        ),
        (
            HttpGetResult(status_code=403, body=b"{}"),
            ProviderOutcomeStatus.ERROR,
            "http_403",
            False,
        ),
        (
            HttpGetResult(status_code=404, body=b"{}"),
            ProviderOutcomeStatus.ERROR,
            "http_404",
            False,
        ),
        (HttpGetResult(status_code=408, body=b""), ProviderOutcomeStatus.TIMEOUT, "timeout", True),
        (
            HttpGetResult(status_code=429, body=b""),
            ProviderOutcomeStatus.RATE_LIMITED,
            "rate_limited",
            True,
        ),
        (
            HttpGetResult(status_code=500, body=b"err"),
            ProviderOutcomeStatus.ERROR,
            "http_5xx",
            True,
        ),
        (
            HttpGetResult(error_code="timeout", retryable=True),
            ProviderOutcomeStatus.TIMEOUT,
            "timeout",
            True,
        ),
        (
            HttpGetResult(error_code="network_failure", retryable=True),
            ProviderOutcomeStatus.ERROR,
            "network_failure",
            True,
        ),
        (
            HttpGetResult(error_code="tls_failure", retryable=False),
            ProviderOutcomeStatus.ERROR,
            "tls_failure",
            False,
        ),
        (
            HttpGetResult(error_code="dns_resolution_failed", retryable=True),
            ProviderOutcomeStatus.ERROR,
            "dns_resolution_failed",
            True,
        ),
        (
            HttpGetResult(error_code="unsafe_resolved_address", retryable=False),
            ProviderOutcomeStatus.ERROR,
            "unsafe_resolved_address",
            False,
        ),
        (
            HttpGetResult(status_code=302, error_code="http_redirect", retryable=False),
            ProviderOutcomeStatus.ERROR,
            "http_redirect",
            False,
        ),
        (
            HttpGetResult(status_code=200, error_code="response_too_large", retryable=False),
            ProviderOutcomeStatus.ERROR,
            "response_too_large",
            False,
        ),
    ],
)
def test_http_status_and_transport_mapping(
    result: HttpGetResult,
    status: ProviderOutcomeStatus,
    code: str,
    retryable: bool,
) -> None:
    http = RecordingHttp(result)
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert outcome.status is status
    assert outcome.error_code == code
    assert outcome.retryable is retryable
    assert outcome.cacheable is False
    assert outcome.found is False
    assert len(http.calls) == 1
    if code == "http_404":
        assert outcome.status is not ProviderOutcomeStatus.NOT_FOUND


def test_adapter_does_not_retry() -> None:
    http = RecordingHttp(HttpGetResult(status_code=429, body=b""))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123"))
    assert len(http.calls) == 1
    assert outcome.status is ProviderOutcomeStatus.RATE_LIMITED


def test_compact_provenance_has_only_safe_metadata() -> None:
    http = RecordingHttp(http_json(_envelope(_feature(_attrs(UNRELATED="nope")))))
    outcome = _provider(http).lookup(_query(parcel_id="ABC123", owner_raw_name=None))
    raw = outcome.raw_response or {}
    dumped = json.dumps(raw)
    assert raw["provider_id"] == "example_arcgis"
    assert raw["source_organization"] == "Example County GIS"
    assert raw["domain"] == "gis.example.gov"
    assert raw["service_path"] == _SERVICE_PATH
    assert raw["layer_id"] == 0
    assert raw["http_status"] == 200
    assert raw["result_count"] == 1
    assert raw["match_mode"] == "exact_parcel"
    assert raw["fields_queried"] == ["PARCELID"]
    assert raw["record_ids"] == ["1"]
    assert outcome.source_url == LAYER_URL
    assert "?" not in (outcome.source_url or "")
    assert "/query" not in (outcome.source_url or "")
    assert "where" not in dumped
    assert "PARCELID = " not in dumped
    assert QUERY_URL not in dumped
    assert "token" not in dumped
    assert "headers" not in dumped
    assert "features" not in dumped
    assert "attributes" not in dumped
    assert "geometry" not in dumped
    assert "ABC123" not in dumped
    assert "nope" not in dumped
    assert "resolved" not in dumped
    full = json.dumps(outcome.model_dump(mode="json"))
    assert "X-App-Token" not in full
