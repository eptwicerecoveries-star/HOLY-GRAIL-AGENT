"""Generic continuation-row folding and identity-gate helpers."""

from __future__ import annotations

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.confidence import ConfidenceModel
from surplus_ai.parser.interpretation.models import (
    ColumnMapping,
    MappingMethod,
    RoutingDecision,
)
from surplus_ai.parser.interpretation.type_inference import has_usable_case_identity
from surplus_ai.parser.row_continuation import fold_stitched_rows


HEADERS = (
    "Tax Deed Number",
    "Sale Date",
    "Sale Amount",
    "Parcel ID",
    "Owner Name",
    "Notes",
)

PRIMARY = (
    "2014001930",
    "9/30/2014",
    "508.74",
    "01452704000230050",
    "TAHIR ANSARI",
    "",
)


def _row(page: int, index: int, cells: tuple[str, ...]) -> tuple[int, int, tuple[str, ...]]:
    return (page, index, cells)


def test_owner_only_continuation_folds_into_previous_owner() -> None:
    wrap = ("", "", "", "", "JR", "")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, wrap)])

    assert len(kept) == 1
    assert kept[0].cells[4] == "TAHIR ANSARI JR"
    assert "," not in kept[0].cells[4]
    assert kept[0].continuation_count == 1


def test_continuation_fills_blank_owner() -> None:
    blank_owner = ("2014001930", "9/30/2014", "508.74", "01452704000230050", "", "")
    wrap = ("", "", "", "", "TAHIR ANSARI", "")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, blank_owner), _row(1, 1, wrap)])

    assert len(kept) == 1
    assert kept[0].cells[4] == "TAHIR ANSARI"


def test_continuation_appends_to_populated_owner() -> None:
    wrap = ("", "", "", "", "ESTATE", "")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, wrap)])

    assert kept[0].cells[4] == "TAHIR ANSARI ESTATE"


def test_two_consecutive_continuations_append_in_order() -> None:
    first = ("", "", "", "", "JOSEPHINE", "")
    second = ("", "", "", "", "LOUISE", "")
    kept = fold_stitched_rows(
        HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, first), _row(1, 2, second)]
    )

    assert len(kept) == 1
    assert kept[0].cells[4] == "TAHIR ANSARI JOSEPHINE LOUISE"
    assert kept[0].continuation_count == 2


def test_three_consecutive_continuations_append_in_order() -> None:
    wraps = [
        ("", "", "", "", "A", ""),
        ("", "", "", "", "B", ""),
        ("", "", "", "", "C", ""),
    ]
    kept = fold_stitched_rows(
        HEADERS, [_row(1, 0, PRIMARY), *(_row(1, i + 1, w) for i, w in enumerate(wraps))]
    )

    assert kept[0].cells[4] == "TAHIR ANSARI A B C"
    assert kept[0].continuation_count == 3


def test_continuation_folds_across_stitched_page_boundary() -> None:
    wrap = ("", "", "", "", "JR", "")
    kept = fold_stitched_rows(HEADERS, [_row(7, 60, PRIMARY), _row(8, 0, wrap)])

    assert len(kept) == 1
    assert kept[0].page_number == 7
    assert kept[0].continuation_sources == ((8, 0),)
    assert kept[0].cells[4] == "TAHIR ANSARI JR"


def test_parcel_band_overflow_routes_to_owner_and_leaves_parcel() -> None:
    wrap = ("", "", "", "LEX", "LLC TR", "")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, wrap)])

    assert len(kept) == 1
    assert kept[0].cells[3] == "01452704000230050"
    assert kept[0].cells[4] == "TAHIR ANSARI LEX LLC TR"


def test_parcel_shaped_candidate_is_not_folded() -> None:
    wrap = ("", "", "", "09-44-23-C3-03725.0060", "SOMEONE", "")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, wrap)])

    assert len(kept) == 2
    assert kept[0].cells == PRIMARY
    assert kept[1].cells[3] == "09-44-23-C3-03725.0060"


def test_new_case_identifier_is_not_folded() -> None:
    next_case = ("2014000702", "10/7/2014", "1768.73", "09452704000160170", "JONES MARY", "")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, next_case)])

    assert len(kept) == 2


def test_new_money_or_date_is_not_folded() -> None:
    dated = ("", "", "", "", "JR", "1/1/2020")
    money = ("", "", "", "", "JR", "12.00")
    dated_kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, dated)])
    money_kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, money)])

    assert len(dated_kept) == 2
    assert len(money_kept) == 2
    assert dated_kept[0].cells[4] == "TAHIR ANSARI"
    assert money_kept[0].cells[4] == "TAHIR ANSARI"


def test_header_like_row_is_not_folded() -> None:
    header = (
        "Tax Deed Number",
        "Sale Date",
        "Sale Amount",
        "Parcel ID",
        "Owner Name",
        "Notes",
    )
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(2, 0, header)])

    assert len(kept) == 2


def test_ambiguous_typed_fragment_is_not_partially_consumed() -> None:
    wrap = ("", "", "", "LEX", "LLC", "6/30/2023")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, wrap)])

    assert len(kept) == 2
    assert kept[0].cells == PRIMARY
    assert kept[1].cells == wrap


def test_no_text_is_lost_and_no_comma_is_invented() -> None:
    wrap = ("", "", "", "", "SMITH JOHN", "")
    kept = fold_stitched_rows(HEADERS, [_row(1, 0, PRIMARY), _row(1, 1, wrap)])

    joined = kept[0].cells[4]
    assert "TAHIR" in joined and "ANSARI" in joined and "SMITH" in joined and "JOHN" in joined
    assert "," not in joined


def test_fold_does_not_take_county_or_page_arguments() -> None:
    assert fold_stitched_rows.__code__.co_varnames[:3] == ("headers", "rows", "registry")


def _mapping(field: CanonicalField | None, header: str = "X") -> ColumnMapping:
    return ColumnMapping(
        original_header=header,
        canonical_field=field,
        method=MappingMethod.EXACT_ALIAS if field else MappingMethod.UNRESOLVED,
        confidence=0.95 if field else 0.0,
    )


def test_identity_gate_accepts_case_certificate_unique_and_parcel() -> None:
    assert has_usable_case_identity({CanonicalField.CASE_NUMBER: "2014-1"})
    assert has_usable_case_identity({CanonicalField.CERTIFICATE_NUMBER: "C-1"})
    assert has_usable_case_identity({CanonicalField.UNIQUE_ID: "99"})
    assert has_usable_case_identity({CanonicalField.PARCEL_ID: "01452704000230050"})


def test_alphabetic_parcel_junk_is_not_identity() -> None:
    assert not has_usable_case_identity({CanonicalField.PARCEL_ID: "LEX"})
    assert not has_usable_case_identity({CanonicalField.OWNER_NAME: "TAHIR ANSARI"})
    assert not has_usable_case_identity({})


def test_owner_only_row_with_high_confidence_still_reviews() -> None:
    model = ConfidenceModel()
    decision = model.route(1.0, ExtractionMethod.TABLE, identity_missing=True)

    assert decision is RoutingDecision.REVIEW


def test_ocr_cap_still_forces_review() -> None:
    model = ConfidenceModel()
    assert model.route(1.0, ExtractionMethod.OCR) is RoutingDecision.REVIEW


def test_valid_identity_still_auto_accepts() -> None:
    model = ConfidenceModel()
    mappings = (_mapping(CanonicalField.CASE_NUMBER),)
    score = model.score(0.95, 1.0, 1.0, mappings)
    assert model.route(score, ExtractionMethod.TABLE, identity_missing=False) is (
        RoutingDecision.AUTO_ACCEPT
    )
