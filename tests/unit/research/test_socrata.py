from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from surplus_ai.research.exceptions import ResearchConfigError
from surplus_ai.research.http import HttpGetResult, ResearchHttpClient
from surplus_ai.research.models import PropertyLookupQuery, ProviderOutcomeStatus
from surplus_ai.research.providers.socrata import SocrataOpenDataProvider, SocrataProviderOptions
from surplus_ai.research.registry import ProviderRegistry
from tests.unit.research.conftest import write_providers_yaml


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def valid_options(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "domain": "opendata.example.gov",
        "dataset_id": "abcd-1234",
        "parcel_field": "parcel_id",
        "owner_field": "owner_name",
        "account_field": "account_id",
        "record_id_field": ":id",
        "query_limit": 10,
        "source_organization": "Example City Open Data",
        "verified_for_automated_access": True,
        "access_reviewed_on": "2026-08-01",
    }
    payload.update(overrides)
    return payload


def options(**overrides: Any) -> SocrataProviderOptions:
    return SocrataProviderOptions.model_validate(valid_options(**overrides))


class RecordingHttp:
    def __init__(self, *results: HttpGetResult) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> HttpGetResult:
        self.calls.append({"url": url, "params": dict(params), "headers": dict(headers)})
        if not self._results:
            raise AssertionError("HTTP was not expected")
        index = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[index]


def http_json(payload: object, status: int = 200) -> HttpGetResult:
    return HttpGetResult(status_code=status, body=json.dumps(payload).encode("utf-8"))


def _query(**overrides: Any) -> PropertyLookupQuery:
    payload = {
        "state": "MD",
        "county_slug": "harford",
        "parcel_id": "01-234567",
        "owner_raw_name": "STEFFEN, DEBORAH",
        "property_address_raw": "123 MAIN ST",
    }
    payload.update(overrides)
    return PropertyLookupQuery.model_validate(payload)


def _provider(
    http: RecordingHttp | ResearchHttpClient,
    **option_overrides: Any,
) -> SocrataOpenDataProvider:
    return SocrataOpenDataProvider(
        "example_socrata",
        options(**option_overrides),
        http=http,
        now=_now,
    )


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        ":id": "row-1",
        "parcel_id": "01-234567",
        "account_id": "A-9",
        "owner_name": "STEFFEN, DEBORAH",
    }
    row.update(overrides)
    return row


def test_socrata_config_valid() -> None:
    parsed = options()
    assert parsed.domain == "opendata.example.gov"
    assert parsed.dataset_id == "abcd-1234"
    assert parsed.verified_for_automated_access is True
    assert parsed.parcel_value_type.value == "text"
    assert parsed.account_value_type.value == "text"


def test_unknown_socrata_option_rejected() -> None:
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(
            valid_options(allow_insecure_http=True)
        )
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(
            valid_options(allow_private_hosts=True)
        )


def test_verified_true_requires_organization_and_date() -> None:
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(
            valid_options(source_organization=None)
        )
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(
            valid_options(access_reviewed_on=None)
        )
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(
            valid_options(access_reviewed_on="2026/08/01")
        )


def test_unverified_does_not_require_review_metadata() -> None:
    parsed = options(
        verified_for_automated_access=False,
        source_organization=None,
        access_reviewed_on=None,
    )
    assert parsed.verified_for_automated_access is False


def test_unverified_provider_is_listable_and_makes_no_http(tmp_path: Any) -> None:
    http = RecordingHttp(http_json([]))
    path = write_providers_yaml(
        tmp_path,
        providers={
            "manual_lookup": {"type": "manual", "description": "t"},
            "example_socrata": {
                "type": "socrata",
                "options": valid_options(verified_for_automated_access=False),
            },
        },
    )
    registry = ProviderRegistry(
        config_path=path, counties_dir=tmp_path / "counties", http_client=http
    )
    assert "example_socrata" in registry.registered_names()
    assert registry.provider_type("example_socrata") == "socrata"
    provider = registry.resolve("example_socrata")
    assert isinstance(provider, SocrataOpenDataProvider)
    outcome = provider.lookup(_query())
    assert http.calls == []
    assert outcome.error_code == "automated_access_not_verified"
    assert outcome.found is False
    assert outcome.cacheable is False
    assert outcome.retryable is False
    assert outcome.requires_human_review is True


def test_shipped_config_has_no_verified_live_provider() -> None:
    registry = ProviderRegistry()
    assert registry.default_provider_name == "manual_lookup"
    for row in registry.describe():
        if row["type"] == "socrata":
            provider = registry.resolve(row["name"])
            assert isinstance(provider, SocrataOpenDataProvider)
            assert provider._options.verified_for_automated_access is False


def test_dataset_and_field_identifiers_rejected_in_config() -> None:
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(dataset_id="not valid")
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(parcel_field="parcel_id = 1")
    with pytest.raises((ValidationError, ResearchConfigError)):
        options(select_fields=["count(*)"])
    parsed = options(record_id_field=":id", select_fields=[":id", "parcel_id"])
    assert parsed.record_id_field == ":id"


def test_apostrophe_is_escaped_and_params_not_concatenated() -> None:
    http = RecordingHttp(http_json([]))
    provider = _provider(http)
    provider.lookup(_query(parcel_id="O'HARA-1"))
    assert len(http.calls) == 1
    where = http.calls[0]["params"]["$where"]
    assert where == "parcel_id = 'O''HARA-1'"
    assert http.calls[0]["url"] == "https://opendata.example.gov/resource/abcd-1234.json"
    assert "?" not in http.calls[0]["url"]


def test_no_arbitrary_soql_from_query_values() -> None:
    http = RecordingHttp(http_json([]))
    provider = _provider(http)
    provider.lookup(_query(parcel_id="x' OR '1'='1"))
    where = http.calls[0]["params"]["$where"]
    assert " OR " not in where.split("=", 1)[0]
    assert where == "parcel_id = 'x'' OR ''1''=''1'"


def test_runtime_cannot_change_endpoint_host() -> None:
    http = RecordingHttp(http_json([]))
    provider = _provider(http)
    provider.lookup(_query(parcel_id="https://evil.example/resource/zzzz-9999.json"))
    assert http.calls[0]["url"] == "https://opendata.example.gov/resource/abcd-1234.json"


def test_query_params_encoded_through_http_client() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["path"] = request.url.path
        captured["host"] = request.url.host
        return httpx.Response(200, json=[])

    client = ResearchHttpClient(transport=httpx.MockTransport(handler))
    try:
        provider = _provider(client)
        provider.lookup(_query(parcel_id="O'HARA"))
    finally:
        client.close()
    assert captured["host"] == "opendata.example.gov"
    assert captured["path"] == "/resource/abcd-1234.json"
    assert captured["url"].startswith("https://opendata.example.gov/resource/abcd-1234.json?")
    assert "O'HARA" not in captured["url"]


def test_exact_parcel_match() -> None:
    http = RecordingHttp(http_json([_row()]))
    outcome = _provider(http).lookup(_query())
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is False
    assert outcome.cacheable is True
    originals = [a.original_value for a in outcome.evidence if a.field == "parcel_id"]
    assert originals == ["01-234567"]


def test_exact_account_match() -> None:
    http = RecordingHttp(http_json([_row(parcel_id="other", account_id="A-9")]))
    outcome = _provider(http).lookup(_query(parcel_id=None, account_id="A-9"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "exact_account"


def test_zero_rows_is_not_found() -> None:
    http = RecordingHttp(http_json([]))
    outcome = _provider(http).lookup(_query())
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND
    assert outcome.found is False
    assert outcome.cacheable is True
    assert outcome.requires_human_review is False


def test_multiple_matches_are_ambiguous_and_cacheable() -> None:
    http = RecordingHttp(
        http_json(
            [
                _row(**{":id": "row-1", "owner_name": "A"}),
                _row(**{":id": "row-2", "owner_name": "B"}),
            ]
        )
    )
    outcome = _provider(http).lookup(_query())
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is True
    assert outcome.cacheable is True
    assert outcome.raw_response is not None
    assert outcome.raw_response["result_count"] == 2
    assert outcome.raw_response["record_ids"] == ["row-1", "row-2"]
    assert outcome.raw_response["match_mode"] == "multiple"
    owners = [a.original_value for a in outcome.evidence if a.field == "owner_name_on_record"]
    assert owners == ["A", "B"]


def test_owner_mismatch_still_property_evidence() -> None:
    http = RecordingHttp(http_json([_row(owner_name="SOME OTHER OWNER")]))
    outcome = _provider(http).lookup(_query())
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is True
    assert outcome.cacheable is True
    owners = [a.original_value for a in outcome.evidence if a.field == "owner_name_on_record"]
    assert "SOME OTHER OWNER" in owners
    assert "STEFFEN, DEBORAH" in owners


def test_missing_identity_makes_no_http() -> None:
    http = RecordingHttp(http_json([]))
    outcome = _provider(http).lookup(_query(parcel_id=None, account_id=None))
    assert http.calls == []
    assert outcome.error_code == "missing_identity"
    assert outcome.retryable is False


def test_owner_only_lookup_is_not_allowed() -> None:
    http = RecordingHttp(http_json([]))
    outcome = _provider(http).lookup(_query(parcel_id=None, account_id=None, owner_raw_name="X"))
    assert http.calls == []
    assert outcome.error_code == "missing_identity"


def test_missing_configured_field_is_schema_mismatch() -> None:
    http = RecordingHttp(http_json([{"parcel_id": "01-234567"}]))
    outcome = _provider(http).lookup(_query())
    assert outcome.error_code == "schema_mismatch"
    assert outcome.retryable is False
    assert outcome.requires_human_review is True
    assert outcome.cacheable is False


@pytest.mark.parametrize(
    ("result", "status", "code", "retryable"),
    [
        (
            HttpGetResult(status_code=400, body=b"[]"),
            ProviderOutcomeStatus.ERROR,
            "http_400",
            False,
        ),
        (
            HttpGetResult(status_code=401, body=b"[]"),
            ProviderOutcomeStatus.ERROR,
            "http_401",
            False,
        ),
        (
            HttpGetResult(status_code=403, body=b"[]"),
            ProviderOutcomeStatus.ERROR,
            "http_403",
            False,
        ),
        (
            HttpGetResult(status_code=404, body=b"[]"),
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
            HttpGetResult(status_code=503, body=b"err"),
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
    outcome = _provider(http).lookup(_query())
    assert outcome.status is status
    assert outcome.error_code == code
    assert outcome.retryable is retryable
    assert outcome.cacheable is False
    assert outcome.found is False
    if code == "http_404":
        assert outcome.status is not ProviderOutcomeStatus.NOT_FOUND


def test_malformed_json() -> None:
    http = RecordingHttp(HttpGetResult(status_code=200, body=b"{not-json"))
    outcome = _provider(http).lookup(_query())
    assert outcome.error_code == "malformed_provider_response"
    assert outcome.retryable is False
    assert outcome.requires_human_review is True


def test_adapter_does_not_retry() -> None:
    http = RecordingHttp(HttpGetResult(status_code=429, body=b""))
    outcome = _provider(http).lookup(_query())
    assert len(http.calls) == 1
    assert outcome.status is ProviderOutcomeStatus.RATE_LIMITED


def test_app_token_sent_but_not_in_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN", "super-secret-app-token")
    http = RecordingHttp(http_json([_row()]))
    provider = _provider(http, optional_credential="SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN")
    outcome = provider.lookup(_query())
    assert http.calls[0]["headers"]["X-App-Token"] == "super-secret-app-token"
    dumped = json.dumps(outcome.model_dump(mode="json"))
    assert "super-secret-app-token" not in dumped
    assert "X-App-Token" not in dumped
    assert outcome.source_url == "https://opendata.example.gov/resource/abcd-1234.json"
    assert "?" not in (outcome.source_url or "")
    assert outcome.raw_response is not None
    assert "$where" not in json.dumps(outcome.raw_response)


def test_optional_token_absent_uses_public_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN", raising=False)
    http = RecordingHttp(http_json([]))
    _provider(http, optional_credential="SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN").lookup(_query())
    assert "X-App-Token" not in http.calls[0]["headers"]


def test_compact_provenance_has_no_query_or_body() -> None:
    http = RecordingHttp(http_json([_row()]))
    outcome = _provider(http).lookup(_query())
    raw = outcome.raw_response or {}
    dumped = json.dumps(raw)
    assert raw["provider_id"] == "example_socrata"
    assert raw["source_organization"] == "Example City Open Data"
    assert raw["domain"] == "opendata.example.gov"
    assert raw["dataset_id"] == "abcd-1234"
    assert raw["http_status"] == 200
    assert raw["result_count"] == 1
    assert raw["match_mode"] == "exact_parcel"
    assert "fields_queried" in raw
    assert "$where" not in dumped
    assert "01-234567" not in dumped
    assert "owner_name" not in dumped or "fields_queried" in dumped
    assert outcome.source_url is not None
    assert "?" not in outcome.source_url


def test_error_detail_does_not_include_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN", "super-secret-app-token")
    http = RecordingHttp(HttpGetResult(status_code=500, body=b"token=super-secret-app-token"))
    outcome = _provider(
        http, optional_credential="SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN"
    ).lookup(_query())
    assert outcome.error_detail is not None
    assert "super-secret-app-token" not in outcome.error_detail
    dumped = json.dumps(outcome.model_dump(mode="json"))
    assert "super-secret-app-token" not in dumped


def test_registry_rejects_unknown_socrata_keys(tmp_path: Any) -> None:
    path = write_providers_yaml(
        tmp_path,
        providers={
            "manual_lookup": {"type": "manual"},
            "example_socrata": {
                "type": "socrata",
                "options": valid_options(allow_insecure_http=True),
            },
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_unverified_does_not_break_registry_load(tmp_path: Any) -> None:
    path = write_providers_yaml(
        tmp_path,
        providers={
            "manual_lookup": {"type": "manual", "description": "t"},
            "example_socrata": {
                "type": "socrata",
                "options": valid_options(verified_for_automated_access=False),
            },
        },
    )
    registry = ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")
    assert registry.resolve("manual_lookup").name == "manual_lookup"
    names = [row["name"] for row in registry.describe()]
    assert "example_socrata" in names
