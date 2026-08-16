from __future__ import annotations

from decimal import localcontext

import pytest
from pydantic import ValidationError

from surplus_ai.research.endpoint import rest_json_number_identity
from surplus_ai.research.models import ProviderOutcomeStatus
from surplus_ai.research.providers.rest_json import RestJsonIdentityValueType
from tests.unit.research.test_rest_json import (
    RecordingHttp,
    _envelope,
    _provider,
    _query,
    _row,
    http_json,
    options,
)


def test_number_identity_config() -> None:
    parsed = options(parcel_value_type="number")
    assert parsed.parcel_value_type is RestJsonIdentityValueType.NUMBER


@pytest.mark.parametrize("value", ["1e3", "+1", "-1", " 1", "1 ", "NaN", "Infinity", "0x10", ""])
def test_number_identity_rejects_invalid_wire_values(value: str) -> None:
    with pytest.raises(ValueError):
        rest_json_number_identity(value)


def test_number_identity_preserves_exact_caller_string() -> None:
    assert rest_json_number_identity("1000160100") == "1000160100"
    assert rest_json_number_identity("01") == "01"
    assert rest_json_number_identity("1.250") == "1.250"


def test_number_identity_no_normalize_context_bug() -> None:
    with localcontext() as ctx:
        ctx.prec = 1
        assert rest_json_number_identity("1000160100") == "1000160100"


def test_number_lookup_sends_exact_validated_string() -> None:
    http = RecordingHttp(http_json(_envelope(_row(parcelId="1000160100"))))
    provider = _provider(http, parcel_value_type="number")
    outcome = provider.lookup(_query(parcel_id="1000160100"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert http.calls[0]["params"]["parcelId"] == "1000160100"


def test_number_local_match_uses_decimal_equality() -> None:
    http = RecordingHttp(http_json(_envelope(_row(parcelId=1000.0))))
    provider = _provider(http, parcel_value_type="number")
    outcome = provider.lookup(_query(parcel_id="1000"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS


def test_number_rejects_exponent_on_lookup() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http, parcel_value_type="number")
    outcome = provider.lookup(_query(parcel_id="1e3"))
    assert outcome.error_code == "invalid_identity_format"
    assert http.calls == []


def test_number_rejects_sign_on_lookup() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http, parcel_value_type="number")
    outcome = provider.lookup(_query(parcel_id="-12"))
    assert outcome.error_code == "invalid_identity_format"
    assert http.calls == []


def test_text_identity_preserved_exactly() -> None:
    http = RecordingHttp(http_json(_envelope(_row(parcelId="Ab-12"))))
    provider = _provider(http)
    outcome = provider.lookup(_query(parcel_id="Ab-12"))
    assert outcome.status is ProviderOutcomeStatus.SUCCESS
    assert http.calls[0]["params"]["parcelId"] == "Ab-12"


def test_text_does_not_rewrite_punctuation() -> None:
    http = RecordingHttp(http_json(_envelope(_row(parcelId="12/34"))))
    provider = _provider(http)
    provider.lookup(_query(parcel_id="12/34"))
    assert http.calls[0]["params"]["parcelId"] == "12/34"


def test_bool_identity_type_rejected() -> None:
    with pytest.raises(ValidationError):
        options(parcel_value_type=True)  # type: ignore[arg-type]


def test_unusable_number_response_schema_mismatch() -> None:
    http = RecordingHttp(http_json(_envelope(_row(parcelId="not-a-number"))))
    provider = _provider(http, parcel_value_type="number")
    outcome = provider.lookup(_query(parcel_id="123"))
    assert outcome.error_code == "schema_mismatch"


def test_blank_number_identity_invalid() -> None:
    http = RecordingHttp(http_json(_envelope(_row())))
    provider = _provider(http, parcel_value_type="number")
    outcome = provider.lookup(_query(parcel_id="   "))
    assert outcome.error_code == "invalid_identity_format"
    assert http.calls == []
