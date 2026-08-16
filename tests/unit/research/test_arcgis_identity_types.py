from __future__ import annotations

import ipaddress
import json
from decimal import localcontext
from typing import Any

import pytest
from pydantic import ValidationError

from surplus_ai.research.http import ResearchHttpClient
from surplus_ai.research.models import ProviderOutcomeStatus
from surplus_ai.research.providers.arcgis import ArcGISIdentityValueType, ArcGISProviderOptions
from tests.unit.research.test_arcgis import (
    RecordingHttp,
    _attrs,
    _envelope,
    _feature,
    _provider,
    _query,
    http_json,
    options,
    valid_options,
)
from tests.unit.research.test_dns import FakeResolver, RecordingBackend

_UNSAFE_NUMBER_VALUES = (
    "+1",
    "-1",
    "1e3",
    "1E3",
    "NaN",
    "Infinity",
    "0x10",
    "1_000",
    "12 34",
    "123abc",
    "'; OR 1=1--",
    "",
    "1 OR 1=1",
)


def test_default_identity_value_types_are_text() -> None:
    parsed = options()
    assert parsed.parcel_value_type is ArcGISIdentityValueType.TEXT
    assert parsed.account_value_type is ArcGISIdentityValueType.TEXT


def test_valid_text_and_number_value_types_accepted() -> None:
    text = options(parcel_value_type="text", account_value_type="text")
    number = options(parcel_value_type="number", account_value_type="number")
    assert text.parcel_value_type is ArcGISIdentityValueType.TEXT
    assert number.parcel_value_type is ArcGISIdentityValueType.NUMBER


def test_unknown_identity_value_type_rejected() -> None:
    with pytest.raises(ValidationError):
        ArcGISProviderOptions.model_validate(valid_options(parcel_value_type="integer"))
    with pytest.raises(ValidationError):
        ArcGISProviderOptions.model_validate(valid_options(parcel_value_type="float"))
    with pytest.raises(ValidationError):
        ArcGISProviderOptions.model_validate(valid_options(parcel_value_type=True))


def test_text_mode_quotes_and_doubles_apostrophes() -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http).lookup(_query(parcel_id="O'Brien"))
    assert http.calls[0]["params"]["where"] == "PARCELID = 'O''Brien'"
    assert "?" not in http.calls[0]["url"]


def test_text_injection_remains_quoted_literal() -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http).lookup(_query(parcel_id="'; OR 1=1--"))
    assert http.calls[0]["params"]["where"] == "PARCELID = '''; OR 1=1--'"
    where = http.calls[0]["params"]["where"]
    assert where.startswith("PARCELID = '")
    assert where.endswith("'")


def test_normal_text_value() -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http).lookup(_query(parcel_id="ABC123"))
    assert http.calls[0]["params"]["where"] == "PARCELID = 'ABC123'"


@pytest.mark.parametrize(
    ("value", "expected_rhs"),
    [
        ("0", "0"),
        ("0.0", "0"),
        ("000", "0"),
        ("00123", "123"),
        ("00100", "100"),
        ("1000", "1000"),
        ("1000160100", "1000160100"),
        ("123.00", "123"),
        ("123.40", "123.4"),
        ("0.1000", "0.1"),
    ],
)
def test_number_query_canonical_rhs(value: str, expected_rhs: str) -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http, parcel_value_type="number").lookup(_query(parcel_id=value))
    where = http.calls[0]["params"]["where"]
    assert where == f"PARCELID = {expected_rhs}"
    assert "'" not in where
    assert "e" not in expected_rhs.lower()


def test_number_query_decimal_context_does_not_change_magnitude() -> None:
    http = RecordingHttp(http_json(_envelope()))
    with localcontext() as ctx:
        ctx.prec = 6
        _provider(http, parcel_value_type="number").lookup(_query(parcel_id="1000160100"))
    assert http.calls[0]["params"]["where"] == "PARCELID = 1000160100"


def test_number_mode_plain_digits_remain_unquoted() -> None:
    http = RecordingHttp(http_json(_envelope()))
    _provider(http, parcel_value_type="number").lookup(_query(parcel_id="123"))
    assert http.calls[0]["params"]["where"] == "PARCELID = 123"
    assert "'" not in http.calls[0]["params"]["where"]
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "value",
    [
        " 123",
        "123 ",
        " 123 ",
        "\t123",
        "123\t",
        "\n123",
        "123\n",
        "1 23",
        "1\t23",
        "   ",
    ],
)
def test_number_mode_rejects_whitespace_without_http(value: str) -> None:
    http = RecordingHttp(http_json(_envelope()))
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id=value))
    assert http.calls == []
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.found is False
    assert outcome.error_code == "invalid_identity_format"
    assert outcome.cacheable is False
    assert outcome.retryable is False
    assert outcome.requires_human_review is True
    assert value not in (outcome.error_detail or "")


@pytest.mark.parametrize("value", _UNSAFE_NUMBER_VALUES)
def test_invalid_number_identity_makes_no_http(value: str) -> None:
    http = RecordingHttp(http_json(_envelope()))
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id=value))
    assert http.calls == []
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.error_code == "invalid_identity_format"
    assert outcome.found is False
    assert outcome.retryable is False
    assert outcome.cacheable is False
    assert outcome.requires_human_review is True
    dumped = json.dumps(outcome.model_dump(mode="json"))
    if value:
        assert value not in (outcome.error_detail or "")
        if len(value) > 2:
            assert value not in dumped


def test_bool_number_identity_is_rejected_without_http() -> None:
    http = RecordingHttp(http_json(_envelope()))
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id="true"))
    assert http.calls == []
    assert outcome.error_code == "invalid_identity_format"


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


def test_number_local_match_string_and_json_number() -> None:
    cases: list[tuple[str, Any]] = [
        ("123", "123"),
        ("00123", "123"),
        ("123.0", "123"),
        ("123", 123),
    ]
    for query_value, returned in cases:
        http = RecordingHttp(
            http_json(_envelope(_feature(_attrs(PARCELID=returned))))
        )
        outcome = _provider(http, parcel_value_type="number").lookup(
            _query(parcel_id=query_value, owner_raw_name=None)
        )
        assert outcome.status is ProviderOutcomeStatus.SUCCESS, (query_value, returned)
        assert outcome.found is True


def test_different_numeric_value_does_not_match() -> None:
    http = RecordingHttp(http_json(_envelope(_feature(_attrs(PARCELID="124")))))
    outcome = _provider(http, parcel_value_type="number").lookup(_query(parcel_id="123"))
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND
    assert outcome.found is False


def test_account_number_mode_independent_of_parcel_text() -> None:
    http = RecordingHttp(
        http_json(_envelope(_feature(_attrs(PARCELID="other", ACCOUNTID="123"))))
    )
    outcome = _provider(
        http,
        parcel_value_type="text",
        account_field="ACCOUNTID",
        account_value_type="number",
    ).lookup(_query(parcel_id=None, account_id="00123"))
    assert http.calls[0]["params"]["where"] == "ACCOUNTID = 123"
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert outcome.raw_response is not None
    assert outcome.raw_response["match_mode"] == "exact_account"
