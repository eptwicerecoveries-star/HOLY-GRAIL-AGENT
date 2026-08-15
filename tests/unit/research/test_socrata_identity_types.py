from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from surplus_ai.research.http import ResearchHttpClient
from surplus_ai.research.models import ProviderOutcomeStatus
from surplus_ai.research.providers.socrata import SocrataIdentityValueType, SocrataProviderOptions
from tests.unit.research.test_dns import FakeResolver, RecordingBackend
from tests.unit.research.test_socrata import (
    RecordingHttp,
    _provider,
    _query,
    _row,
    http_json,
    options,
    valid_options,
)

_UNSAFE_NUMBER_VALUES = (
    "1 OR 1=1",
    "1;DROP",
    "1e6",
    "+123",
    "-123",
    "NaN",
    "Infinity",
    "0x10",
    "1_000",
    "12 34",
    "123abc",
    "",
)


def test_default_identity_value_types_are_text() -> None:
    parsed = options()
    assert parsed.parcel_value_type is SocrataIdentityValueType.TEXT
    assert parsed.account_value_type is SocrataIdentityValueType.TEXT


def test_valid_text_and_number_value_types_accepted() -> None:
    text = options(parcel_value_type="text", account_value_type="text")
    number = options(parcel_value_type="number", account_value_type="number")
    assert text.parcel_value_type is SocrataIdentityValueType.TEXT
    assert number.parcel_value_type is SocrataIdentityValueType.NUMBER
    assert number.account_value_type is SocrataIdentityValueType.NUMBER


def test_unknown_identity_value_type_rejected() -> None:
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(valid_options(parcel_value_type="integer"))
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(valid_options(account_value_type="numeric"))
    with pytest.raises(ValidationError):
        SocrataProviderOptions.model_validate(valid_options(parcel_value_type=True))


def test_text_mode_quoted_equality_and_apostrophe_escape_unchanged() -> None:
    http = RecordingHttp(http_json([]))
    _provider(http, parcel_value_type="text").lookup(_query(parcel_id="O'HARA-1"))
    assert http.calls[0]["params"]["$where"] == "parcel_id = 'O''HARA-1'"
    assert "?" not in http.calls[0]["url"]


def test_text_injection_remains_literal_text() -> None:
    http = RecordingHttp(http_json([]))
    _provider(http).lookup(_query(parcel_id="x' OR '1'='1"))
    assert http.calls[0]["params"]["$where"] == "parcel_id = 'x'' OR ''1''=''1'"


def test_number_query_produces_unquoted_canonical_literal() -> None:
    http = RecordingHttp(http_json([]))
    _provider(http, parcel_value_type="number").lookup(_query(parcel_id="1000160100"))
    assert http.calls[0]["params"]["$where"] == "parcel_id = 1000160100"
    assert "'" not in http.calls[0]["params"]["$where"]


def test_number_query_preserves_pluto_shaped_integer_identity() -> None:
    http = RecordingHttp(http_json([]))
    _provider(
        http,
        parcel_field="bbl",
        parcel_value_type="number",
        owner_field="ownername",
        situs_address_field="address",
        account_field=None,
        record_id_field=None,
        verified_for_automated_access=True,
    ).lookup(_query(parcel_id="1000160100"))
    where = http.calls[0]["params"]["$where"]
    assert where == "bbl = 1000160100"
    assert where != "bbl = 10001601"
    assert where != "bbl = '1000160100'"
    assert "'" not in where


@pytest.mark.parametrize(
    ("value", "expected_rhs"),
    [
        ("0", "0"),
        ("0.0", "0"),
        ("000", "0"),
        ("000.000", "0"),
        ("1", "1"),
        ("10", "10"),
        ("100", "100"),
        ("1000", "1000"),
        ("00100", "100"),
        ("1000160100", "1000160100"),
        ("123.0", "123"),
        ("123.00", "123"),
        ("123.40", "123.4"),
        ("123.4500", "123.45"),
        ("0.10", "0.1"),
        ("0.1000", "0.1"),
        ("10.01", "10.01"),
        ("100.01", "100.01"),
    ],
)
def test_number_query_canonical_rhs_for_required_identities(
    value: str, expected_rhs: str
) -> None:
    http = RecordingHttp(http_json([]))
    _provider(http, parcel_value_type="number").lookup(_query(parcel_id=value))
    where = http.calls[0]["params"]["$where"]
    assert where == f"parcel_id = {expected_rhs}"
    assert "'" not in where
    assert "e" not in expected_rhs.lower()
    assert "e+" not in where.lower()
    assert "e-" not in where.lower()


def test_number_query_decimal_and_leading_zeros_canonicalize() -> None:
    http = RecordingHttp(http_json([]))
    _provider(http, parcel_value_type="number").lookup(_query(parcel_id="123.45"))
    assert http.calls[0]["params"]["$where"] == "parcel_id = 123.45"
    http = RecordingHttp(http_json([]))
    _provider(http, parcel_value_type="number").lookup(_query(parcel_id="00123"))
    assert http.calls[0]["params"]["$where"] == "parcel_id = 123"


def test_number_query_passed_through_httpx_params_not_concatenated() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["path"] = request.url.path
        captured["host"] = request.url.host
        return httpx.Response(200, json=[])

    client = ResearchHttpClient(transport=httpx.MockTransport(handler))
    try:
        _provider(client, parcel_value_type="number").lookup(_query(parcel_id="1000160100"))
    finally:
        client.close()
    parts = urlsplit(captured["url"])
    assert captured["host"] == "opendata.example.gov"
    assert captured["path"] == "/resource/abcd-1234.json"
    assert parts.query
    assert parse_qs(parts.query)["$where"] == ["parcel_id = 1000160100"]


@pytest.mark.parametrize("value", _UNSAFE_NUMBER_VALUES)
def test_invalid_number_identity_makes_no_http(value: str) -> None:
    http = RecordingHttp(http_json([]))
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id=value))
    assert http.calls == []
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.error_code == "invalid_identity_format"
    assert outcome.found is False
    assert outcome.retryable is False
    assert outcome.cacheable is False
    assert outcome.requires_human_review is True
    dumped = json.dumps(outcome.model_dump(mode="json"))
    assert "$where" not in dumped
    if value:
        assert value not in (outcome.error_detail or "")
        if value not in "abcd-1234":
            assert value not in dumped


def test_invalid_number_identity_makes_no_dns_or_connect() -> None:
    resolver = FakeResolver([ipaddress.IPv4Address("8.8.8.8")])
    backend = RecordingBackend()
    client = ResearchHttpClient(resolver=resolver, network_backend=backend)
    try:
        outcome = _provider(client, parcel_value_type="number").lookup(
            _query(parcel_id="1 OR 1=1")
        )
    finally:
        client.close()
    assert resolver.calls == []
    assert backend.calls == []
    assert outcome.error_code == "invalid_identity_format"
    assert "1 OR 1=1" not in (outcome.error_detail or "")


def test_numeric_identity_does_not_change_destination_host() -> None:
    http = RecordingHttp(http_json([]))
    _provider(http, parcel_value_type="number").lookup(
        _query(parcel_id="1000160100")
    )
    assert http.calls[0]["url"] == "https://opendata.example.gov/resource/abcd-1234.json"


def test_number_local_match_string_and_json_number() -> None:
    cases: list[tuple[str, Any]] = [
        ("123", "123"),
        ("00123", "123"),
        ("123.0", "123"),
        ("123", 123),
    ]
    for query_value, returned in cases:
        http = RecordingHttp(http_json([_row(parcel_id=returned)]))
        outcome = _provider(http, parcel_value_type="number").lookup(
            _query(parcel_id=query_value)
        )
        assert outcome.status is ProviderOutcomeStatus.SUCCESS, (query_value, returned)
        assert outcome.found is True
        assert outcome.requires_human_review is False
        assert outcome.cacheable is True
        originals = [a.original_value for a in outcome.evidence if a.field == "parcel_id"]
        assert originals == [str(returned)]


def test_number_match_preserves_provider_string_representation() -> None:
    http = RecordingHttp(
        http_json(
            [
                {
                    ":id": "row-1",
                    "parcel_id": "1000160100",
                    "account_id": "A-9",
                    "owner_name": "Example Owner",
                }
            ]
        )
    )
    outcome = _provider(http, parcel_value_type="number").lookup(
        _query(parcel_id="1000160100", owner_raw_name=None)
    )
    originals = [a.original_value for a in outcome.evidence if a.field == "parcel_id"]
    assert originals == ["1000160100"]


def test_different_numeric_value_does_not_match() -> None:
    http = RecordingHttp(http_json([_row(parcel_id="124")]))
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id="123"))
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND
    assert outcome.found is False


def test_long_integer_identity_matches_exact_string_not_truncated() -> None:
    http = RecordingHttp(http_json([_row(parcel_id="1000160100")]))
    outcome = _provider(http, parcel_value_type="number").lookup(
        _query(parcel_id="1000160100")
    )
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    originals = [a.original_value for a in outcome.evidence if a.field == "parcel_id"]
    assert originals == ["1000160100"]

    http = RecordingHttp(http_json([_row(parcel_id="10001601")]))
    truncated = _provider(http, parcel_value_type="number").lookup(
        _query(parcel_id="1000160100")
    )
    assert truncated.status is ProviderOutcomeStatus.NOT_FOUND
    assert truncated.found is False


def test_malformed_returned_number_is_schema_mismatch() -> None:
    http = RecordingHttp(http_json([_row(parcel_id="123abc")]))
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id="123"))
    assert outcome.error_code == "schema_mismatch"
    assert outcome.retryable is False
    assert outcome.cacheable is False
    assert outcome.requires_human_review is True
    assert outcome.found is False


def test_account_number_mode_independent_of_parcel_text() -> None:
    http = RecordingHttp(http_json([_row(parcel_id="other", account_id="123")]))
    outcome = _provider(
        http,
        parcel_value_type="text",
        account_value_type="number",
    ).lookup(_query(parcel_id=None, account_id="00123"))
    assert http.calls[0]["params"]["$where"] == "account_id = 123"
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "exact_account"


def test_account_text_mode_remains_quoted() -> None:
    http = RecordingHttp(http_json([]))
    _provider(
        http,
        parcel_value_type="number",
        account_value_type="text",
    ).lookup(_query(parcel_id=None, account_id="A-9"))
    assert http.calls[0]["params"]["$where"] == "account_id = 'A-9'"


def test_parcel_number_type_is_not_applied_to_account_lookup() -> None:
    http = RecordingHttp(http_json([]))
    _provider(
        http,
        parcel_value_type="number",
        account_value_type="text",
    ).lookup(_query(parcel_id=None, account_id="00123"))
    assert http.calls[0]["params"]["$where"] == "account_id = '00123'"


def test_account_value_type_does_not_create_lookup_without_account_field() -> None:
    http = RecordingHttp(http_json([]))
    outcome = _provider(
        http,
        account_field=None,
        account_value_type="number",
    ).lookup(_query(parcel_id=None, account_id="123"))
    assert http.calls == []
    assert outcome.error_code == "missing_identity"


def test_multiple_numeric_matches_are_ambiguous() -> None:
    http = RecordingHttp(
        http_json(
            [
                _row(**{":id": "row-1", "parcel_id": "123", "owner_name": "A"}),
                _row(**{":id": "row-2", "parcel_id": 123, "owner_name": "B"}),
            ]
        )
    )
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id="123.0"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is True
    assert outcome.cacheable is True
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "multiple"
    assert outcome.raw_response["result_count"] == 2


def test_pluto_shaped_offline_number_identity_fixture() -> None:
    http = RecordingHttp(
        http_json(
            [
                {
                    "bbl": "1000160100",
                    "ownername": "Example Owner",
                    "address": "Example Address",
                }
            ]
        )
    )
    outcome = _provider(
        http,
        parcel_field="bbl",
        parcel_value_type="number",
        owner_field="ownername",
        situs_address_field="address",
        account_field=None,
        record_id_field=None,
        verified_for_automated_access=True,
    ).lookup(_query(parcel_id="1000160100", owner_raw_name=None))
    assert http.calls[0]["params"]["$where"] == "bbl = 1000160100"
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.found is True
    assert outcome.requires_human_review is False
    parcels = [a.original_value for a in outcome.evidence if a.field == "parcel_id"]
    owners = [a.original_value for a in outcome.evidence if a.field == "owner_name_on_record"]
    addresses = [a.original_value for a in outcome.evidence if a.field == "current_address"]
    assert parcels == ["1000160100"]
    assert owners == ["Example Owner"]
    assert addresses == ["Example Address"]
    assert outcome.raw_response is not None
    dumped = json.dumps(outcome.raw_response)
    assert "1000160100" not in dumped
    assert "Example Owner" not in dumped
