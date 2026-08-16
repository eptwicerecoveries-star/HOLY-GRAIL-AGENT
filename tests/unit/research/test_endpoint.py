from __future__ import annotations

from decimal import localcontext

import pytest

from surplus_ai.research.endpoint import (
    arcgis_layer_url,
    arcgis_number_literal,
    arcgis_query_url,
    rest_json_number_identity,
    rest_json_resource_url,
    socrata_resource_url,
    soql_number_literal,
    soql_string_literal,
    sql_string_literal,
    validate_arcgis_domain,
    validate_arcgis_layer_id,
    validate_arcgis_service_path,
    validate_public_https_url,
    validate_rest_json_domain,
    validate_rest_json_field_key,
    validate_rest_json_path,
    validate_rest_json_query_param,
    validate_rest_json_records_path,
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


def test_arcgis_number_literal_accepts_plain_digits() -> None:
    assert arcgis_number_literal("123") == "123"
    assert "'" not in arcgis_number_literal("123")


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
        "",
    ],
)
def test_arcgis_number_literal_rejects_whitespace(value: str) -> None:
    with pytest.raises(ValueError):
        arcgis_number_literal(value)


def test_sql_string_literal_doubles_apostrophes() -> None:
    assert sql_string_literal("O'Brien") == "'O''Brien'"
    assert sql_string_literal("'; OR 1=1--") == "'''; OR 1=1--'"


def test_arcgis_domain_validation() -> None:
    assert validate_arcgis_domain("GIS.Example.GOV") == "gis.example.gov"
    for domain in (
        "https://gis.example.gov",
        "8.8.8.8",
        "127.0.0.1",
        "localhost",
        "gis.example.gov/path",
        " gis.example.gov",
        "gis.example.gov:443",
        "user:pass@gis.example.gov",
    ):
        with pytest.raises(ResearchConfigError):
            validate_arcgis_domain(domain)


def test_arcgis_service_path_validation() -> None:
    valid = "/arcgis/rest/services/Example/Parcels/FeatureServer"
    assert validate_arcgis_service_path(valid) == valid
    for path in (
        "arcgis/rest/services/Example/Parcels/FeatureServer",
        "/arcgis/rest/services/Example/Parcels/FeatureServer/",
        "/arcgis/services/Example/Parcels/FeatureServer",
        "/arcgis/rest/services/Example/Parcels/MapServer",
        "/arcgis/rest/services/Example/Parcels/FeatureServer?f=json",
        "/arcgis/rest/services/Example/Parcels/FeatureServer#x",
        "/arcgis/rest/services/Example%2FParcels/FeatureServer",
        "/arcgis/rest/services/Example/../Parcels/FeatureServer",
        "/arcgis/rest/services/./Parcels/FeatureServer",
        "/arcgis/rest/services/Example\\Parcels/FeatureServer",
        "/arcgis//rest/services/Example/Parcels/FeatureServer",
        "/arcgis/rest/services/Example@x/FeatureServer",
        "/arcgis/rest/services/Example:x/FeatureServer",
        "https://gis.example.gov/arcgis/rest/services/Example/Parcels/FeatureServer",
    ):
        with pytest.raises(ResearchConfigError):
            validate_arcgis_service_path(path)
    too_long = "/arcgis/rest/services/" + ("A" * 500) + "/FeatureServer"
    with pytest.raises(ResearchConfigError):
        validate_arcgis_service_path(too_long)


def test_arcgis_layer_id_validation() -> None:
    assert validate_arcgis_layer_id(0) == 0
    assert validate_arcgis_layer_id(9999) == 9999
    for value in (-1, 10000, True, False, "0"):
        with pytest.raises(ResearchConfigError):
            validate_arcgis_layer_id(value)


def test_arcgis_urls_are_canonical() -> None:
    layer = arcgis_layer_url(
        "gis.example.gov",
        "/arcgis/rest/services/Example/Parcels/FeatureServer",
        0,
    )
    query = arcgis_query_url(
        "gis.example.gov",
        "/arcgis/rest/services/Example/Parcels/FeatureServer",
        0,
    )
    assert layer == (
        "https://gis.example.gov/arcgis/rest/services/Example/Parcels/FeatureServer/0"
    )
    assert query == layer + "/query"
    assert "?" not in layer
    assert "?" not in query


def test_rest_json_domain_validation() -> None:
    assert validate_rest_json_domain("API.Example.GOV") == "api.example.gov"
    for domain in (
        "https://api.example.gov",
        "8.8.8.8",
        "127.0.0.1",
        "localhost",
        "api.example.gov/path",
        " api.example.gov",
        "api.example.gov:443",
        "user:pass@api.example.gov",
    ):
        with pytest.raises(ResearchConfigError):
            validate_rest_json_domain(domain)


def test_rest_json_path_validation() -> None:
    assert validate_rest_json_path("/v1/parcels") == "/v1/parcels"
    assert validate_rest_json_path("/") == "/"
    for path in (
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
    ):
        with pytest.raises(ResearchConfigError):
            validate_rest_json_path(path)
    too_long = "/v1/" + ("A" * 520)
    with pytest.raises(ResearchConfigError):
        validate_rest_json_path(too_long)


def test_rest_json_query_param_validation() -> None:
    assert validate_rest_json_query_param("parcelId") == "parcelId"
    assert validate_rest_json_query_param("parcel.id-v1") == "parcel.id-v1"
    for name in ("", "parcel Id", "a&b", "a=b", "a?b", "a#b", "a[b]", "1bad"):
        with pytest.raises(ResearchConfigError):
            validate_rest_json_query_param(name)


def test_rest_json_field_key_validation() -> None:
    assert validate_rest_json_field_key("parcelId") == "parcelId"
    assert validate_rest_json_field_key("owner-name") == "owner-name"
    for name in ("", "parcel.Id", "a[b]", "a/b", "a b", ".parcel"):
        with pytest.raises(ResearchConfigError):
            validate_rest_json_field_key(name)


def test_rest_json_records_path_validation() -> None:
    assert validate_rest_json_records_path(None) == ()
    assert validate_rest_json_records_path(["results", "records"]) == (
        "results",
        "records",
    )
    with pytest.raises(ResearchConfigError):
        validate_rest_json_records_path(["a", "b", "c", "d", "e", "f"])
    with pytest.raises(ResearchConfigError):
        validate_rest_json_records_path(["*"])
    with pytest.raises(ResearchConfigError):
        validate_rest_json_records_path([""])


def test_rest_json_resource_url_has_no_query() -> None:
    url = rest_json_resource_url("api.example.gov", "/v1/parcels")
    assert url == "https://api.example.gov/v1/parcels"
    assert "?" not in url


def test_rest_json_number_identity_preserves_caller_string() -> None:
    assert rest_json_number_identity("01") == "01"
    assert rest_json_number_identity("1000160100") == "1000160100"
    with pytest.raises(ValueError):
        rest_json_number_identity("1e3")
    with pytest.raises(ValueError):
        rest_json_number_identity(" 1")
