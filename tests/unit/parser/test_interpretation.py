"""Tests for column interpretation, typing and confidence routing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.interpretation.alias_registry import (
    load_alias_registry,
    normalize_header,
)
from surplus_ai.parser.interpretation.canonical import (
    MONEY_FIELDS,
    CanonicalField,
    FieldKind,
    kind_of,
)
from surplus_ai.parser.interpretation.column_interpreter import ColumnInterpreter
from surplus_ai.parser.interpretation.confidence import ConfidenceModel, mapping_confidence
from surplus_ai.parser.interpretation.county_config import CountyConfig
from surplus_ai.parser.interpretation.models import (
    ColumnMapping,
    MappingMethod,
    RoutingDecision,
    SurplusResolution,
    SurplusSource,
)
from surplus_ai.parser.interpretation.type_inference import (
    infer_field_kind,
    looks_like_address,
    looks_like_person_name,
    parse_date,
    parse_money,
)

ABSENT = SurplusResolution(source=SurplusSource.ABSENT)


# --------------------------------------------------------------------------------------
# Header normalization
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACCT #", "account number"),
        ("Acct. No.", "account number"),
        ("account number", "account number"),
        ("  Parcel   Number  ", "parcel number"),
        ("TAX MAP ID", "tax map id"),
        ("Owner/Name", "owner name"),
        ("Amt", "amount"),
        ("Parcel No", "parcel number"),
        ("Case No.", "case number"),
    ],
)
def test_normalize_header(raw: str, expected: str) -> None:
    assert normalize_header(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [("No Sale", "no sale"), ("NOTES", "notes")])
def test_negation_is_not_mistaken_for_an_abbreviation(raw: str, expected: str) -> None:
    """ "No" only means "number" when it carries a period or ends the label."""
    assert normalize_header(raw) == expected


def test_positional_suffix_is_ignored_when_matching() -> None:
    """Duplicate columns get __2 suffixes at extraction; they still resolve."""
    assert normalize_header("Amount__2") == "amount"


# --------------------------------------------------------------------------------------
# Alias resolution
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Parcel Number", CanonicalField.PARCEL_ID),
        ("Account Number", CanonicalField.PARCEL_ID),
        ("Tax Map ID", CanonicalField.PARCEL_ID),
        ("ACCT #", CanonicalField.PARCEL_ID),
        ("Owner", CanonicalField.OWNER_NAME),
        ("Property Owner", CanonicalField.OWNER_NAME),
        ("Owner Name", CanonicalField.OWNER_NAME),
        ("Property Owner at Time of Sale", CanonicalField.OWNER_NAME),
        ("Sale Date", CanonicalField.SALE_DATE),
        ("Sold Date", CanonicalField.SALE_DATE),
        ("Last Known Address", CanonicalField.OWNER_MAILING_ADDRESS),
    ],
)
def test_aliases_map_to_one_canonical_field(header: str, expected: CanonicalField) -> None:
    """The requirement in the brief: different county wordings, one universal field."""
    assert load_alias_registry().match_exact(header) is expected


def test_surplus_is_absent_from_the_alias_registry() -> None:
    """Surplus must not be reachable by aliasing or fuzzy matching."""
    registry = load_alias_registry()

    assert registry.match_exact("Surplus") is None
    assert registry.match_exact("Excess Funds") is None


def test_money_concepts_stay_distinct() -> None:
    registry = load_alias_registry()

    assert registry.match_exact("Sale Amount") is CanonicalField.SALE_AMOUNT
    assert registry.match_exact("Winning Bid") is CanonicalField.WINNING_BID
    assert registry.match_exact("Assessment") is CanonicalField.ASSESSED_VALUE
    assert registry.match_exact("Face Value") is CanonicalField.FACE_VALUE_AMOUNT
    assert registry.match_exact("Refunded Overbid") is CanonicalField.REFUNDED_AMOUNT
    assert registry.match_exact("Remaining Overbid") is CanonicalField.REMAINING_AMOUNT


def test_every_money_field_has_its_own_identity() -> None:
    assert len(MONEY_FIELDS) >= 14
    assert CanonicalField.SURPLUS_AMOUNT in MONEY_FIELDS
    assert kind_of(CanonicalField.SURPLUS_AMOUNT) is FieldKind.MONEY


# --------------------------------------------------------------------------------------
# Type inference
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,234.56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("$0.00", Decimal("0.00")),
        ("491.33", Decimal("491.33")),
        ("$16,798.00", Decimal("16798.00")),
        ("(500.00)", Decimal("-500.00")),
        ("133,000", Decimal("133000")),
    ],
)
def test_parse_money(raw: str, expected: Decimal) -> None:
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "N/A", "none", "GANTT THOMAS", "-", "abc"])
def test_parse_money_rejects_non_money(raw: str) -> None:
    assert parse_money(raw) is None


def test_zero_is_a_real_amount_not_a_missing_one() -> None:
    """A fully refunded overbid genuinely reads $0.00 and must not read as unknown."""
    parsed = parse_money("$0.00")

    assert parsed == Decimal("0.00")
    assert parsed is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10/02/2023", date(2023, 10, 2)),
        ("1/8/2014", date(2014, 1, 8)),
        ("2023-10-02", date(2023, 10, 2)),
        ("10/02/2023 09:40:45 AM EDT", date(2023, 10, 2)),
        ("09/23/2024 10:06:45 AM EDT", date(2024, 9, 23)),
    ],
)
def test_parse_date(raw: str, expected: date) -> None:
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "not a date", "$1,234.56"])
def test_parse_date_rejects_non_dates(raw: str) -> None:
    assert parse_date(raw) is None


def test_person_and_entity_names_are_distinguished() -> None:
    assert looks_like_person_name("GANTT THOMAS")
    assert looks_like_person_name("WHYTE, WALTER & CATHERINE")
    assert not looks_like_person_name("Macallan Properties, LLC")
    assert not looks_like_person_name("The Tax Lien Hedge LLC")


def test_addresses_are_recognised() -> None:
    assert looks_like_address("608 DEMBYTOWN ROAD, JOPPA, MD 21085")
    assert not looks_like_address("SURPLUS")


def test_infer_field_kind_from_values() -> None:
    assert infer_field_kind(["$1.00", "$2.00", "$3.00", "$4.00"]) is FieldKind.MONEY
    assert infer_field_kind(["1/1/2020", "2/2/2021", "3/3/2022"]) is FieldKind.DATE
    assert infer_field_kind([]) is None


def test_inference_declines_on_mixed_columns() -> None:
    assert infer_field_kind(["$1.00", "abc", "1/1/2020", "xyz"]) is None


# --------------------------------------------------------------------------------------
# Column interpretation precedence
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def interpreter() -> ColumnInterpreter:
    return ColumnInterpreter()


def test_county_override_wins(interpreter: ColumnInterpreter) -> None:
    config = CountyConfig(
        county_name="Test",
        state="XX",
        column_overrides={"Owner": CanonicalField.PURCHASER_NAME},
    )

    mappings = interpreter.interpret(("Owner",), {"Owner": ["A B"]}, ABSENT, config)

    assert mappings[0].canonical_field is CanonicalField.PURCHASER_NAME
    assert mappings[0].method is MappingMethod.COUNTY_OVERRIDE
    assert mappings[0].confidence == 1.0


def test_exact_alias_beats_fuzzy(interpreter: ColumnInterpreter) -> None:
    mappings = interpreter.interpret(("Parcel Number",), {"Parcel Number": []}, ABSENT)

    assert mappings[0].method is MappingMethod.EXACT_ALIAS
    assert mappings[0].canonical_field is CanonicalField.PARCEL_ID


def test_unresolved_columns_are_preserved_not_dropped(interpreter: ColumnInterpreter) -> None:
    mappings = interpreter.interpret(
        ("Blorptastic Widget",), {"Blorptastic Widget": ["zz", "yy"]}, ABSENT
    )

    assert len(mappings) == 1
    assert mappings[0].original_header == "Blorptastic Widget"
    assert mappings[0].canonical_field is None
    assert mappings[0].method is MappingMethod.UNRESOLVED


def test_unlabelled_money_column_is_never_assigned_a_money_field(
    interpreter: ColumnInterpreter,
) -> None:
    """Knowing a column holds money says nothing about which money it is."""
    values = ["$1.00", "$2.00", "$3.00", "$4.00", "$5.00"]

    mappings = interpreter.interpret(("Zzz",), {"Zzz": values}, ABSENT)

    assert mappings[0].canonical_field is None


def test_value_inference_proposes_non_money_fields(interpreter: ColumnInterpreter) -> None:
    values = ["1/1/2020", "2/2/2021", "3/3/2022", "4/4/2023"]

    mappings = interpreter.interpret(("Zzz",), {"Zzz": values}, ABSENT)

    assert mappings[0].canonical_field is CanonicalField.SALE_DATE
    assert mappings[0].method is MappingMethod.VALUE_INFERENCE


def test_named_non_sale_date_headers_are_not_inferred_as_sale_date(
    interpreter: ColumnInterpreter,
) -> None:
    dates = ["1/1/2020", "2/2/2021", "3/3/2022", "4/4/2023"]

    mappings = interpreter.interpret(
        ("Balance Date", "Lienholder Claim Period Expires", "Sale Date"),
        {
            "Balance Date": dates,
            "Lienholder Claim Period Expires": dates,
            "Sale Date": dates,
        },
        ABSENT,
    )

    by_header = {m.original_header: m for m in mappings}
    assert by_header["Balance Date"].canonical_field is None
    assert by_header["Balance Date"].method is MappingMethod.UNRESOLVED
    assert by_header["Lienholder Claim Period Expires"].canonical_field is None
    assert by_header["Lienholder Claim Period Expires"].method is MappingMethod.UNRESOLVED
    assert by_header["Sale Date"].canonical_field is CanonicalField.SALE_DATE
    assert by_header["Sale Date"].method is MappingMethod.EXACT_ALIAS


def test_tax_deed_number_maps_to_case_number(interpreter: ColumnInterpreter) -> None:
    mappings = interpreter.interpret(
        ("Tax Deed Number",),
        {"Tax Deed Number": ["2014001930", "2014000702", "2014001622"]},
        ABSENT,
    )

    assert mappings[0].canonical_field is CanonicalField.CASE_NUMBER
    assert mappings[0].method is MappingMethod.EXACT_ALIAS
    assert normalize_header("Tax Deed Number") == "tax deed number"


def test_surplus_column_without_an_alias_maps_to_surplus_amount(
    interpreter: ColumnInterpreter,
) -> None:
    surplus = SurplusResolution(
        source=SurplusSource.EXPLICIT, source_column="SURPLUS", is_explicit=True
    )

    mappings = interpreter.interpret(("SURPLUS",), {"SURPLUS": ["$1.00"]}, surplus)

    assert mappings[0].canonical_field is CanonicalField.SURPLUS_AMOUNT
    assert mappings[0].method is MappingMethod.SURPLUS_EXPLICIT


def test_every_header_produces_exactly_one_mapping(interpreter: ColumnInterpreter) -> None:
    headers = ("A", "Parcel Number", "Owner", "Zzz")

    mappings = interpreter.interpret(headers, dict.fromkeys(headers, []), ABSENT)

    assert tuple(m.original_header for m in mappings) == headers


# --------------------------------------------------------------------------------------
# Confidence and routing
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def confidence() -> ConfidenceModel:
    return ConfidenceModel()


def _mapping(conf: float) -> ColumnMapping:
    return ColumnMapping(
        original_header="x",
        canonical_field=CanonicalField.PARCEL_ID,
        method=MappingMethod.EXACT_ALIAS,
        confidence=conf,
    )


def test_mapping_confidence_counts_unresolved_columns() -> None:
    resolved = (_mapping(1.0), _mapping(1.0))
    half = (
        _mapping(1.0),
        ColumnMapping(
            original_header="y",
            canonical_field=None,
            method=MappingMethod.UNRESOLVED,
            confidence=0.0,
        ),
    )

    assert mapping_confidence(resolved) == 1.0
    assert mapping_confidence(half) == 0.5


def test_high_scores_auto_accept(confidence: ConfidenceModel) -> None:
    score = confidence.score(0.95, 1.0, 1.0, (_mapping(0.95),))

    assert confidence.route(score, ExtractionMethod.TABLE) is RoutingDecision.AUTO_ACCEPT


def test_low_scores_quarantine(confidence: ConfidenceModel) -> None:
    assert confidence.route(0.2, ExtractionMethod.TABLE) is RoutingDecision.QUARANTINE


def test_ocr_rows_never_auto_accept(confidence: ConfidenceModel) -> None:
    """A misread digit in a money field is not something a high score rules out."""
    assert confidence.route(1.0, ExtractionMethod.OCR) is RoutingDecision.REVIEW


def test_unresolved_surplus_never_auto_accepts(confidence: ConfidenceModel) -> None:
    """Every other field may be perfect; the figure the business exists to find is not."""
    decision = confidence.route(1.0, ExtractionMethod.TABLE, surplus_unresolved=True)

    assert decision is RoutingDecision.REVIEW


def test_coercion_failures_prevent_auto_accept(confidence: ConfidenceModel) -> None:
    assert confidence.route(1.0, ExtractionMethod.TABLE, coercion_failures=1) is (
        RoutingDecision.REVIEW
    )


def test_quarantine_wins_over_the_ocr_cap(confidence: ConfidenceModel) -> None:
    assert confidence.route(0.1, ExtractionMethod.OCR) is RoutingDecision.QUARANTINE


def test_score_is_bounded(confidence: ConfidenceModel) -> None:
    score = confidence.score(1.0, 1.0, 1.0, (_mapping(1.0),))

    assert 0.0 <= score <= 1.0
    assert score == pytest.approx(1.0)
