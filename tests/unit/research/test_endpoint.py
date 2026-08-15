from __future__ import annotations

from decimal import localcontext

import pytest

from surplus_ai.research.endpoint import (
    socrata_resource_url,
    soql_number_literal,
    soql_string_literal,
    validate_public_https_url,
    validate_socrata_dataset_id,
    validate_socrata_domain,
    validate_socrata_field_id,
)
from surplus_ai.research.exceptions import ResearchConfigError


def test_https_public_hostname_accepted() -> None:
    url = validate_public_https_url("https://opendata.example.gov/resource/abcd-1234.json")
    assert url.startswith("https://opendata.example.gov/")


@pytest.mark.parametrize(
    "url",
    [
        "http://opendata.example.gov/resource/abcd-1234.json",
        "file:///etc/passwd",
        "ftp://opendata.example.gov/resource/abcd-1234.json",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "https://localhost/resource/abcd-1234.json",
        "https://127.0.0.1/resource/abcd-1234.json",
        "https://[::1]/resource/abcd-1234.json",
        "https://10.0.0.5/resource/abcd-1234.json",
        "https://192.168.1.8/resource/abcd-1234.json",
        "https://[fc00::1]/resource/abcd-1234.json",
        "https://169.254.1.1/resource/abcd-1234.json",
        "https://[fe80::1]/resource/abcd-1234.json",
        "https://0.0.0.0/resource/abcd-1234.json",
        "https://100.64.0.1/resource/abcd-1234.json",
        "https://224.0.0.1/resource/abcd-1234.json",
    ],
)
def test_blocked_urls_rejected(url: str) -> None:
    with pytest.raises(ResearchConfigError):
        validate_public_https_url(url)


@pytest.mark.parametrize(
    "domain",
    [
        "localhost",
        "127.0.0.1",
        "::1",
        "10.1.2.3",
        "192.168.0.9",
        "169.254.10.20",
        "100.64.0.1",
        "fc00::1",
        "http://opendata.example.gov",
        "opendata.example.gov/resource",
    ],
)
def test_blocked_socrata_domains_rejected(domain: str) -> None:
    with pytest.raises(ResearchConfigError):
        validate_socrata_domain(domain)


def test_socrata_resource_url_has_no_query() -> None:
    url = socrata_resource_url("opendata.example.gov", "abcd-1234")
    assert url == "https://opendata.example.gov/resource/abcd-1234.json"
    assert "?" not in url


def test_dataset_id_validation() -> None:
    assert validate_socrata_dataset_id("Abcd-1234") == "abcd-1234"
    with pytest.raises(ResearchConfigError):
        validate_socrata_dataset_id("not-a-dataset")
    with pytest.raises(ResearchConfigError):
        validate_socrata_dataset_id("abcd1234")
    with pytest.raises(ResearchConfigError):
        validate_socrata_dataset_id("abcd-1234/extra")


def test_field_identifier_validation() -> None:
    assert validate_socrata_field_id("parcel_id") == "parcel_id"
    assert validate_socrata_field_id(":id", allow_system_id=True) == ":id"
    with pytest.raises(ResearchConfigError):
        validate_socrata_field_id(":id")
    with pytest.raises(ResearchConfigError):
        validate_socrata_field_id("owner_name;select")
    with pytest.raises(ResearchConfigError):
        validate_socrata_field_id("count(*)")
    with pytest.raises(ResearchConfigError):
        validate_socrata_field_id("a = 1")
    with pytest.raises(ResearchConfigError):
        validate_socrata_field_id(":created_at", allow_system_id=True)


def test_soql_string_literal_escapes_apostrophe() -> None:
    assert soql_string_literal("O'HARA") == "'O''HARA'"


@pytest.mark.parametrize(
    ("value", "expected"),
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
        ("00123", "123"),
        ("1000160100", "1000160100"),
        ("123.0", "123"),
        ("123.00", "123"),
        ("123.40", "123.4"),
        ("123.45", "123.45"),
        ("123.4500", "123.45"),
        ("0.10", "0.1"),
        ("0.1000", "0.1"),
        ("10.01", "10.01"),
        ("100.01", "100.01"),
    ],
)
def test_soql_number_literal_is_unquoted_and_canonical(value: str, expected: str) -> None:
    literal = soql_number_literal(value)
    assert literal == expected
    assert "'" not in literal
    assert "e" not in literal.lower()
    assert literal != ""


def test_soql_number_literal_preserves_integer_magnitude_under_low_precision() -> None:
    with localcontext() as ctx:
        ctx.prec = 6
        assert soql_number_literal("1000160100") == "1000160100"
        assert soql_number_literal("1000") == "1000"
        assert soql_number_literal("100") == "100"


@pytest.mark.parametrize(
    "value",
    [
        "1 OR 1=1",
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
        "1;DROP",
    ],
)
def test_soql_number_literal_rejects_unsafe_input(value: str) -> None:
    with pytest.raises(ValueError):
        soql_number_literal(value)
