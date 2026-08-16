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
from surplus_ai.research.providers.rest_json import (
    RestJsonIdentityValueType,
    RestJsonProvider,
    RestJsonProviderOptions,
)
from surplus_ai.research.registry import COUNTIES_CONFIG_DIR, ProviderRegistry, ProviderSpec
from tests.unit.research.conftest import write_providers_yaml
from tests.unit.research.test_arcgis import valid_options as arcgis_valid_options
from tests.unit.research.test_socrata import (
    RecordingHttp,
    _query,
)
from tests.unit.research.test_socrata import (
    valid_options as socrata_valid_options,
)

RESOURCE_URL = "https://api.example.gov/v1/parcels"


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def valid_options(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "domain": "api.example.gov",
        "path": "/v1/parcels",
        "parcel_query_param": "parcelId",
        "parcel_field": "parcelId",
        "owner_field": "ownerName",
        "situs_address_field": "propertyAddress",
        "record_id_field": "recordId",
        "select_fields": ["parcelId", "ownerName", "propertyAddress", "recordId"],
        "records_path": ["results"],
        "query_limit": 5,
        "source_organization": "Example County",
        "verified_for_automated_access": True,
        "access_reviewed_on": "2026-08-01",
    }
    payload.update(overrides)
    return payload


def options(**overrides: Any) -> RestJsonProviderOptions:
    return RestJsonProviderOptions.model_validate(valid_options(**overrides))


def _provider(
    http: RecordingHttp | ResearchHttpClient,
    **option_overrides: Any,
) -> RestJsonProvider:
    return RestJsonProvider(
        "example_rest_json",
        options(**option_overrides),
        http=http,
        now=_now,
    )


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "parcelId": "ABC-123",
        "ownerName": "ACME HOLDINGS LLC",
        "propertyAddress": "123 MAIN ST",
        "recordId": "r-1",
    }
    row.update(overrides)
    return row


def _envelope(*rows: dict[str, Any], path: list[str] | None = None) -> object:
    if path is None:
        path = ["results"]
    if not path:
        return list(rows)
    current: object = list(rows)
    for key in reversed(path):
        current = {key: current}
    return current


def http_json(payload: object, status: int = 200) -> HttpGetResult:
    return HttpGetResult(status_code=status, body=json.dumps(payload).encode("utf-8"))


def test_rest_json_config_valid() -> None:
    parsed = options()
    assert parsed.domain == "api.example.gov"
    assert parsed.path == "/v1/parcels"
    assert parsed.parcel_query_param == "parcelId"
    assert parsed.parcel_value_type is RestJsonIdentityValueType.TEXT
    assert parsed.verified_for_automated_access is True
    dumped = parsed.model_dump()
    assert "token" not in dumped
    assert "headers" not in dumped
    assert "url" not in dumped
    assert "method" not in dumped


def test_unknown_rest_json_option_rejected() -> None:
    for extra in (
        {"token": "secret"},
        {"headers": {"Authorization": "Bearer x"}},
        {"url": "https://api.example.gov/v1/parcels"},
        {"method": "POST"},
        {"static_query": {"foo": "bar"}},
        {"optional_credential": "SURPLUS_AI_REST_TOKEN"},
    ):
        with pytest.raises(ValidationError):
            RestJsonProviderOptions.model_validate(valid_options(**extra))


@pytest.mark.parametrize(
    "domain",
    [
        "https://api.example.gov",
        "api.example.gov/v1",
        "8.8.8.8",
        "127.0.0.1",
        "localhost",
        " api.example.gov",
        "api.example.gov ",
        "api.example.gov:443",
        "user@api.example.gov",
        "",
        "not a host",
    ],
)
def test_invalid_domain_rejected(domain: str) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(domain=domain)


@pytest.mark.parametrize(
    "path",
    [
        "v1/parcels",
        "/v1/parcels/",
        "/v1/parcels?x=1",
        "/v1/parcels#frag",
        "/v1/parcels%2Fextra",
        "/v1/../parcels",
        "/v1/./parcels",
        "/v1\\parcels",
        "/v1//parcels",
        "https://api.example.gov/v1/parcels",
        "/v1/{parcel}",
        "/v1/${parcel}",
        "/v1/:parcel",
        "",
    ],
)
def test_invalid_path_rejected(path: str) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(path=path)


def test_root_path_allowed() -> None:
    parsed = options(path="/")
    assert parsed.path == "/"


@pytest.mark.parametrize(
    "name",
    ["", "parcel Id", "parcel&id", "parcel=id", "parcel?id", "parcel#id", "parcel[id]", "1parcel"],
)
def test_invalid_query_param_rejected(name: str) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(parcel_query_param=name)


def test_query_param_allows_dot_and_hyphen() -> None:
    parsed = options(parcel_query_param="parcel.id-v1")
    assert parsed.parcel_query_param == "parcel.id-v1"


@pytest.mark.parametrize(
    "name",
    ["parcel.Id", "parcel[id]", "parcel/id", "parcel id", ".parcel", ""],
)
def test_invalid_field_key_rejected(name: str) -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(parcel_field=name)


def test_select_fields_must_include_mapped_fields() -> None:
    with pytest.raises(ValidationError):
        options(select_fields=["parcelId", "ownerName"])


def test_account_query_param_requires_account_field() -> None:
    with pytest.raises(ValidationError):
        options(account_query_param="accountId")
    with pytest.raises(ValidationError):
        options(
            account_field="accountId",
            select_fields=[
                "parcelId",
                "ownerName",
                "propertyAddress",
                "recordId",
                "accountId",
            ],
        )


def test_verified_requires_metadata() -> None:
    with pytest.raises(ValidationError):
        options(source_organization=None)
    with pytest.raises(ValidationError):
        options(access_reviewed_on=None)


def test_unverified_skips_network() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http, verified_for_automated_access=False, access_reviewed_on=None)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "automated_access_not_verified"
    assert outcome.found is False
    assert http.calls == []


def test_example_config_loads() -> None:
    registry = ProviderRegistry()
    assert registry.provider_type("example_rest_json") == "rest_json"
    provider = registry.resolve("example_rest_json")
    assert isinstance(provider, RestJsonProvider)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "automated_access_not_verified"


def test_successful_lookup_top_level_array() -> None:
    http = RecordingHttp(http_json([_row()]))
    provider = _provider(http, records_path=[])
    outcome = provider.lookup(_query(parcel_id="ABC-123", owner_raw_name="ACME HOLDINGS LLC"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == RESOURCE_URL
    assert http.calls[0]["params"] == {"parcelId": "ABC-123"}
    assert http.calls[0]["headers"] == {}
    fields = {atom.field for atom in outcome.evidence}
    assert fields == {
        "parcel_id",
        "owner_name_on_record",
        "current_address",
        "property_record_id",
    }
    assert outcome.raw_response["match_mode"] == "exact_parcel"
    assert outcome.raw_response["path"] == "/v1/parcels"
    assert "fields_inspected" in outcome.raw_response
    assert "ABC-123" not in json.dumps(outcome.raw_response)


def test_records_path_nested() -> None:
    payload = {"results": {"records": [_row()]}}
    http = RecordingHttp(http_json(payload))
    provider = _provider(http, records_path=["results", "records"])
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True


def test_limit_query_param_sent_when_configured() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http, limit_query_param="limit")
    provider.lookup(_query(parcel_id="ABC-123"))
    assert http.calls[0]["params"] == {"parcelId": "ABC-123", "limit": "5"}


def test_no_limit_query_param_by_default() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http)
    provider.lookup(_query(parcel_id="ABC-123"))
    assert "limit" not in http.calls[0]["params"]
    assert "$limit" not in http.calls[0]["params"]


def test_apostrophe_identity_encoded_via_params() -> None:
    http = RecordingHttp(http_json(_envelope(_row(parcelId="O'BRIEN"))))
    provider = _provider(http)
    provider.lookup(_query(parcel_id="O'BRIEN"))
    assert http.calls[0]["params"]["parcelId"] == "O'BRIEN"
    assert "'" not in http.calls[0]["url"]


def test_empty_list_not_found() -> None:
    http = RecordingHttp(http_json(_envelope()))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND
    assert outcome.found is False
    assert outcome.cacheable is True


def test_nonmatch_not_found() -> None:
    http = RecordingHttp(http_json(_envelope(_row(parcelId="OTHER"))))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND


def test_multiple_response_one_local_match() -> None:
    http = RecordingHttp(
        http_json(_envelope(_row(parcelId="OTHER"), _row(parcelId="ABC-123", recordId="r-2")))
    )
    provider = _provider(http)
    outcome = provider.lookup(
        _query(parcel_id="ABC-123", owner_raw_name="ACME HOLDINGS LLC")
    )
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is False
    assert outcome.raw_response["match_mode"] == "exact_parcel"


def test_ambiguous_local_matches_follow_existing_convention() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _row(parcelId="ABC-123", recordId="r-1"),
                _row(parcelId="ABC-123", recordId="r-2", ownerName="OTHER LLC"),
            )
        )
    )
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is True
    assert outcome.raw_response["match_mode"] == "multiple"


def test_result_incomplete_before_matching() -> None:
    rows = [_row(parcelId=f"P-{i}", recordId=f"r-{i}") for i in range(6)]
    http = RecordingHttp(http_json(_envelope(*rows)))
    provider = _provider(http, query_limit=5)
    outcome = provider.lookup(_query(parcel_id="P-0"))
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.error_code == "result_incomplete"
    assert outcome.found is False
    assert outcome.cacheable is False
    assert outcome.retryable is False
    assert outcome.requires_human_review is True
    assert outcome.evidence == ()
    assert len(http.calls) == 1


def test_exact_query_limit_accepted() -> None:
    rows = [_row(parcelId=f"P-{i}", recordId=f"r-{i}") for i in range(5)]
    rows[2] = _row(parcelId="ABC-123", recordId="r-match")
    http = RecordingHttp(http_json(_envelope(*rows)))
    provider = _provider(http, query_limit=5)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS


def test_record_not_object_malformed() -> None:
    http = RecordingHttp(http_json({"results": ["not-an-object"]}))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "malformed_provider_response"


def test_missing_records_path_malformed() -> None:
    http = RecordingHttp(http_json({"other": [_row()]}))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "malformed_provider_response"


def test_non_object_path_segment_malformed() -> None:
    http = RecordingHttp(http_json({"results": "not-object"}))
    provider = _provider(http, records_path=["results", "records"])
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "malformed_provider_response"


def test_final_value_not_list_malformed() -> None:
    http = RecordingHttp(http_json({"results": {"parcelId": "ABC-123"}}))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "malformed_provider_response"


def test_top_level_object_without_path_malformed() -> None:
    http = RecordingHttp(http_json({"parcelId": "ABC-123"}))
    provider = _provider(http, records_path=[])
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "malformed_provider_response"


def test_invalid_json_malformed() -> None:
    http = RecordingHttp(HttpGetResult(status_code=200, body=b"{not-json"))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "malformed_provider_response"


def test_required_field_absent_schema_mismatch() -> None:
    http = RecordingHttp(http_json(_envelope({"parcelId": "ABC-123", "ownerName": "X"})))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "schema_mismatch"


def test_nested_mapped_value_schema_mismatch() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _row(ownerName={"nested": "nope"}),
            )
        )
    )
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "schema_mismatch"


def test_extra_fields_ignored() -> None:
    http = RecordingHttp(http_json(_envelope(_row(secret="do-not-persist", ssn="000"))))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    blob = json.dumps(outcome.raw_response)
    assert "secret" not in blob
    assert "ssn" not in blob
    assert "do-not-persist" not in blob


def test_account_lookup() -> None:
    http = RecordingHttp(
        http_json(
            _envelope(
                _row(accountId="A-9", parcelId="OTHER"),
            )
        )
    )
    provider = _provider(
        http,
        account_query_param="accountId",
        account_field="accountId",
        select_fields=[
            "parcelId",
            "ownerName",
            "propertyAddress",
            "recordId",
            "accountId",
        ],
    )
    outcome = provider.lookup(_query(parcel_id=None, account_id="A-9"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert http.calls[0]["params"] == {"accountId": "A-9"}
    assert any(atom.field == "account_id" for atom in outcome.evidence)


def test_mailing_address_mapped() -> None:
    http = RecordingHttp(http_json(_envelope(_row(mailingAddress="PO BOX 1"))))
    provider = _provider(
        http,
        mailing_address_field="mailingAddress",
        select_fields=[
            "parcelId",
            "ownerName",
            "propertyAddress",
            "recordId",
            "mailingAddress",
        ],
    )
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert any(atom.field == "mailing_address" for atom in outcome.evidence)


@pytest.mark.parametrize(
    ("status", "error_code", "retryable"),
    [
        (301, "http_redirect", False),
        (400, "http_400", False),
        (401, "http_401", False),
        (403, "http_403", False),
        (404, "http_404", False),
        (500, "http_5xx", True),
        (503, "http_5xx", True),
    ],
)
def test_http_status_mapping(status: int, error_code: str, retryable: bool) -> None:
    http = RecordingHttp(HttpGetResult(status_code=status, body=b""))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == error_code
    assert outcome.retryable is retryable
    assert outcome.found is False
    assert outcome.cacheable is False
    assert "body" not in (outcome.raw_response or {})


def test_http_429_rate_limited() -> None:
    http = RecordingHttp(HttpGetResult(status_code=429, body=b"slow down"))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.RATE_LIMITED
    assert outcome.error_code == "rate_limited"
    assert outcome.retryable is True
    assert "slow down" not in json.dumps(outcome.raw_response)


def test_transport_timeout() -> None:
    http = RecordingHttp(HttpGetResult(error_code="timeout", retryable=True))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.status is ProviderOutcomeStatus.TIMEOUT
    assert outcome.retryable is True


def test_network_failure() -> None:
    http = RecordingHttp(HttpGetResult(error_code="network_failure", retryable=True))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "network_failure"
    assert outcome.retryable is True


def test_tls_failure() -> None:
    http = RecordingHttp(HttpGetResult(error_code="tls_failure", retryable=False))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    assert outcome.error_code == "tls_failure"
    assert outcome.retryable is False


def test_provenance_compact() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="ABC-123"))
    raw = outcome.raw_response
    assert set(raw) >= {
        "provider_id",
        "source_organization",
        "domain",
        "path",
        "http_status",
        "retrieved_at",
        "result_count",
        "match_mode",
        "fields_inspected",
        "record_ids",
    }
    assert "resolved_ip" not in raw
    assert "headers" not in raw
    assert "request_params" not in raw
    assert "body" not in raw


def test_cross_provider_options_rejected() -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        ProviderSpec.model_validate({"type": "rest_json", "options": socrata_valid_options()})
    with pytest.raises((ValidationError, ResearchConfigError)):
        ProviderSpec.model_validate({"type": "rest_json", "options": arcgis_valid_options()})
    with pytest.raises((ValidationError, ResearchConfigError)):
        ProviderSpec.model_validate({"type": "socrata", "options": valid_options()})
    with pytest.raises((ValidationError, ResearchConfigError)):
        ProviderSpec.model_validate({"type": "arcgis", "options": valid_options()})
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate(
            {
                "type": "socrata",
                "options": RestJsonProviderOptions.model_validate(valid_options()),
            }
        )


def test_rest_json_type_requires_options() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate({"type": "rest_json"})


def test_manual_rejects_rest_options() -> None:
    with pytest.raises(ValidationError):
        ProviderSpec.model_validate({"type": "manual", "options": valid_options()})


def test_registry_loads_example_and_keeps_others_disabled() -> None:
    registry = ProviderRegistry()
    assert registry.default_provider_name == "manual_lookup"
    assert registry.provider_type("example_rest_json") == "rest_json"
    assert registry.provider_type("lake_county_fl_pa_tax_parcels") == "arcgis"
    assert registry.provider_type("franklin_county_oh_auditor_parcels") == "arcgis"
    assert registry.provider_type("nyc_dcp_pluto") == "socrata"
    for name in (
        "example_rest_json",
        "lake_county_fl_pa_tax_parcels",
        "franklin_county_oh_auditor_parcels",
        "nyc_dcp_pluto",
    ):
        provider = registry.resolve(name)
        outcome = provider.lookup(_query(parcel_id="ABC-123"))
        assert outcome.error_code == "automated_access_not_verified"
    for path in COUNTIES_CONFIG_DIR.rglob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        research = raw.get("research") if isinstance(raw, dict) else None
        if isinstance(research, dict):
            assert research.get("property_provider") not in {
                "example_rest_json",
                "lake_county_fl_pa_tax_parcels",
                "franklin_county_oh_auditor_parcels",
                "nyc_dcp_pluto",
            }


def test_registry_builds_verified_rest_json(tmp_path: Path) -> None:
    write_providers_yaml(
        tmp_path,
        providers={
            "manual_lookup": {"type": "manual", "description": "manual"},
            "demo_rest": {
                "type": "rest_json",
                "description": "demo",
                "options": valid_options(),
            },
        },
    )
    http = RecordingHttp(http_json(_envelope(_row())))
    registry = ProviderRegistry(config_path=tmp_path / "providers.yaml", http_client=http)
    provider = registry.resolve("demo_rest")
    assert isinstance(provider, RestJsonProvider)
    outcome = provider.lookup(_query(parcel_id="ABC-123", owner_raw_name="ACME HOLDINGS LLC"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert len(http.calls) == 1


def test_query_limit_rejects_bool() -> None:
    with pytest.raises(ValidationError):
        options(query_limit=True)  # type: ignore[arg-type]


def test_records_path_depth_bound() -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(records_path=["a", "b", "c", "d", "e", "f"])


def test_one_request_only_on_success() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http)
    provider.lookup(_query(parcel_id="ABC-123"))
    assert len(http.calls) == 1
