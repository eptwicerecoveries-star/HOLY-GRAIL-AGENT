"""Generic Parcel ID typed-cell cleanup. No county, file, or page branching."""

from __future__ import annotations

import inspect
from pathlib import Path

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.confidence import ConfidenceModel
from surplus_ai.parser.interpretation.models import (
    ColumnMapping,
    MappingMethod,
    RoutingDecision,
    SurplusResolution,
    SurplusSource,
)
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.interpretation.typed_cell_cleanup import (
    REVIEW_REASON_TYPED_CELL,
    apply_parcel_id_cell_cleanup,
)
from surplus_ai.parser.models import RawRow
from surplus_ai.parser.persistence import review_reason
from surplus_ai.parser.row_continuation import fold_stitched_rows

ABSENT = SurplusResolution(source=SurplusSource.ABSENT)
HYPHENATED = "09-44-23-C3-03725.0060"
COMPACT = "01452704000230050"
SPACED = "01 234 567"


def _mapping(field: CanonicalField | None, header: str) -> ColumnMapping:
    return ColumnMapping(
        original_header=header,
        canonical_field=field,
        method=MappingMethod.EXACT_ALIAS if field else MappingMethod.UNRESOLVED,
        confidence=0.95 if field else 0.0,
    )


def _right_owner_mappings() -> tuple[ColumnMapping, ...]:
    return (
        _mapping(CanonicalField.CASE_NUMBER, "Tax Deed Number"),
        _mapping(CanonicalField.PARCEL_ID, "Parcel ID"),
        _mapping(CanonicalField.OWNER_NAME, "Owner Name"),
    )


def _left_owner_mappings() -> tuple[ColumnMapping, ...]:
    return (
        _mapping(CanonicalField.OWNER_NAME, "Owner Name"),
        _mapping(CanonicalField.PARCEL_ID, "Parcel ID"),
        _mapping(CanonicalField.CASE_NUMBER, "Tax Deed Number"),
    )


def _parcel_only_mappings() -> tuple[ColumnMapping, ...]:
    return (
        _mapping(CanonicalField.CASE_NUMBER, "Tax Deed Number"),
        _mapping(CanonicalField.PARCEL_ID, "Parcel ID"),
        _mapping(CanonicalField.SALE_DATE, "Sale Date"),
    )


def _canonical(
    parcel: str,
    owner: str | None = "THOMAS",
    case: str = "2014001930",
) -> dict[CanonicalField, object]:
    values: dict[CanonicalField, object] = {
        CanonicalField.CASE_NUMBER: case,
        CanonicalField.PARCEL_ID: parcel,
    }
    if owner is not None:
        values[CanonicalField.OWNER_NAME] = owner
    return values


def _raw_row(values: dict[str, str]) -> RawRow:
    return RawRow(
        values=values,
        source_pdf_path=Path("fixture.pdf"),
        source_pdf_sha256="a" * 64,
        page_number=1,
        table_index=0,
        row_index_on_page=0,
        extraction_method=ExtractionMethod.TABLE,
        extraction_strategy="test",
        confidence=0.95,
    )


def _interpret(values: dict[str, str], mappings: tuple[ColumnMapping, ...]):
    return InterpretationPipeline()._interpret_row(_raw_row(values), mappings, ABSENT, 0.95)


def test_alphabetic_suffix_is_peeled() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} SMITH"), _right_owner_mappings()
    )

    assert result.peeled is True
    assert result.ambiguous is False
    assert result.updates[CanonicalField.PARCEL_ID] == HYPHENATED
    assert result.updates[CanonicalField.OWNER_NAME] == "SMITH THOMAS"


def test_valid_prefix_is_preserved_exactly() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{COMPACT} JONES"), _right_owner_mappings()
    )

    assert result.updates[CanonicalField.PARCEL_ID] == COMPACT
    assert COMPACT in f"{COMPACT} JONES"


def test_suffix_fills_blank_owner() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} SMITH", owner=""), _right_owner_mappings()
    )

    assert result.updates[CanonicalField.OWNER_NAME] == "SMITH"


def test_suffix_prepends_when_owner_is_to_the_right() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} SMITH", owner="JOHN THOMAS"), _right_owner_mappings()
    )

    assert result.updates[CanonicalField.OWNER_NAME] == "SMITH JOHN THOMAS"
    assert "," not in result.updates[CanonicalField.OWNER_NAME]


def test_suffix_appends_when_owner_is_to_the_left() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} JR", owner="SMITH JOHN"), _left_owner_mappings()
    )

    assert result.updates[CanonicalField.OWNER_NAME] == "SMITH JOHN JR"


def test_internal_separators_are_not_split() -> None:
    result = apply_parcel_id_cell_cleanup(_canonical(HYPHENATED), _right_owner_mappings())

    assert result.peeled is False
    assert result.updates == {}
    assert result.review_reasons == ()


def test_internal_spaces_in_a_valid_identifier_are_kept() -> None:
    result = apply_parcel_id_cell_cleanup(_canonical(SPACED), _right_owner_mappings())

    assert result.peeled is False
    assert result.ambiguous is False
    assert result.updates == {}


def test_numeric_suffix_fails_closed() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{COMPACT} 001"), _right_owner_mappings()
    )

    assert result.peeled is False
    assert result.ambiguous is True
    assert result.updates == {}
    assert result.review_reasons == (REVIEW_REASON_TYPED_CELL,)


def test_date_like_suffix_fails_closed() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} 1/1/2020"), _right_owner_mappings()
    )

    assert result.peeled is False
    assert result.ambiguous is True


def test_money_like_suffix_fails_closed() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} $12.00"), _right_owner_mappings()
    )

    assert result.peeled is False
    assert result.ambiguous is True


def test_identifier_like_suffix_fails_closed() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{COMPACT} {HYPHENATED}"), _right_owner_mappings()
    )

    assert result.peeled is False
    assert result.ambiguous is True


def test_no_compatible_destination_fails_closed() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} SMITH", owner=None), _parcel_only_mappings()
    )

    assert result.peeled is False
    assert result.ambiguous is True
    assert CanonicalField.OWNER_NAME not in result.updates


def test_raw_parcel_id_is_unchanged_after_peel() -> None:
    original = f"{HYPHENATED} SMITH"
    interpreted = _interpret(
        {"Tax Deed Number": "2014001930", "Parcel ID": original, "Owner Name": "THOMAS"},
        _right_owner_mappings(),
    )

    assert interpreted.raw_values["Parcel ID"] == original
    assert interpreted.canonical_values[CanonicalField.PARCEL_ID] == HYPHENATED


def test_raw_owner_is_unchanged_while_canonical_owner_changes() -> None:
    interpreted = _interpret(
        {
            "Tax Deed Number": "2014001930",
            "Parcel ID": f"{HYPHENATED} SMITH",
            "Owner Name": "THOMAS",
        },
        _right_owner_mappings(),
    )

    assert interpreted.raw_values["Owner Name"] == "THOMAS"
    assert interpreted.canonical_values[CanonicalField.OWNER_NAME] == "SMITH THOMAS"
    assert interpreted.routing is RoutingDecision.AUTO_ACCEPT


def test_cleanup_does_not_take_county_or_source_arguments() -> None:
    names = inspect.signature(apply_parcel_id_cell_cleanup).parameters
    forbidden = {"county", "state", "url", "filename", "page", "page_number", "path"}
    assert forbidden.isdisjoint(names)
    source = Path(__file__).resolve().parents[3] / "surplus_ai" / "parser" / "interpretation" / "typed_cell_cleanup.py"
    text = source.read_text(encoding="utf-8").casefold()
    assert "lee" not in text
    assert "florida" not in text
    assert "http" not in text


def test_fail_closed_reviews_even_when_case_number_exists() -> None:
    interpreted = _interpret(
        {
            "Tax Deed Number": "2014001930",
            "Parcel ID": f"{COMPACT} 001",
            "Owner Name": "THOMAS",
        },
        _right_owner_mappings(),
    )

    assert interpreted.routing is RoutingDecision.REVIEW
    assert REVIEW_REASON_TYPED_CELL in interpreted.review_reasons
    assert interpreted.canonical_values[CanonicalField.PARCEL_ID] == f"{COMPACT} 001"
    assert interpreted.canonical_values[CanonicalField.CASE_NUMBER] == "2014001930"


def test_typed_cell_ambiguous_route_cap() -> None:
    model = ConfidenceModel()
    decision = model.route(1.0, ExtractionMethod.TABLE, typed_cell_ambiguous=True)

    assert decision is RoutingDecision.REVIEW


def test_review_reason_mentions_typed_cell_ambiguity() -> None:
    interpreted = _interpret(
        {
            "Tax Deed Number": "2014001930",
            "Parcel ID": f"{HYPHENATED} 1/1/2020",
            "Owner Name": "THOMAS",
        },
        _right_owner_mappings(),
    )

    text = review_reason(interpreted, "")
    assert "typed identifier cell" in text.casefold()


def test_name_punctuation_suffix_is_peeled() -> None:
    result = apply_parcel_id_cell_cleanup(
        _canonical(f"{HYPHENATED} SMITH,", owner="JOHN"), _right_owner_mappings()
    )

    assert result.peeled is True
    assert result.updates[CanonicalField.PARCEL_ID] == HYPHENATED
    assert result.updates[CanonicalField.OWNER_NAME] == "SMITH, JOHN"


def test_slash_and_dot_parcels_stay_intact() -> None:
    dotted = "12-34-56-78.90"
    slashed = "01/02/03-04"
    for parcel in (dotted, slashed):
        result = apply_parcel_id_cell_cleanup(_canonical(parcel), _right_owner_mappings())
        assert result.peeled is False
        assert result.updates == {}


def test_continuation_fold_still_appends_wrap_lines() -> None:
    headers = (
        "Tax Deed Number",
        "Sale Date",
        "Sale Amount",
        "Parcel ID",
        "Owner Name",
        "Notes",
    )
    primary = ("2014001930", "9/30/2014", "508.74", COMPACT, "TAHIR ANSARI", "")
    wrap = ("", "", "", "", "JR", "")
    kept = fold_stitched_rows(headers, [(1, 0, primary), (1, 1, wrap)])

    assert len(kept) == 1
    assert kept[0].cells[4] == "TAHIR ANSARI JR"
