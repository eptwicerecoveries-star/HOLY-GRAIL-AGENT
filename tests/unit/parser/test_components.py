"""Unit tests for parser components in isolation, using synthetic inputs.

The corpus tests prove behaviour on real counties; these pin the building blocks against
edge cases the current five PDFs happen not to contain.
"""

from __future__ import annotations

import pytest

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.exceptions import HeaderDetectionError, ParserError
from surplus_ai.parser.headers import HeaderReconstructor, disambiguate
from surplus_ai.parser.models import ExtractedTable
from surplus_ai.parser.pipeline import pair_cells_with_headers
from surplus_ai.parser.quality import (
    column_consistency,
    fill_rate,
    infer_kind,
    row_header_score,
    score_table,
    type_coherence,
)
from surplus_ai.parser.stitching import TableStitcher
from surplus_ai.parser.strategies.base import normalize_cell
from surplus_ai.parser.strategies.word_cluster import (
    column_index_for,
    extract_tables_from_word_pages,
    find_column_boundaries,
    geometry_alignment_score,
    group_words_into_lines,
    rows_from_words,
)
from surplus_ai.utils.exceptions import AppError


def _table(rows: list[list[str]], page: int = 1, index: int = 0) -> ExtractedTable:
    return ExtractedTable(
        page_number=page,
        table_index=index,
        rows=tuple(tuple(r) for r in rows),
        strategy="test",
    )


def _word(text: str, x0: float, x1: float, top: float) -> dict[str, float | str]:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 8}


# --------------------------------------------------------------------------------------
# normalize_cell
# --------------------------------------------------------------------------------------


def test_normalize_cell_rejoins_wrapped_text() -> None:
    assert normalize_cell("SALE\nAMOUNT") == "SALE AMOUNT"


def test_normalize_cell_handles_none_and_blank() -> None:
    assert normalize_cell(None) == ""
    assert normalize_cell("   \n  ") == ""


def test_normalize_cell_preserves_case_and_punctuation() -> None:
    assert normalize_cell("  ACCT #  ") == "ACCT #"
    assert normalize_cell("O'Brien, John Jr.") == "O'Brien, John Jr."


def test_normalize_cell_does_not_alter_currency() -> None:
    assert normalize_cell("$1,234.56") == "$1,234.56"


# --------------------------------------------------------------------------------------
# quality signals
# --------------------------------------------------------------------------------------


def test_column_consistency_rewards_uniform_rows() -> None:
    uniform = (("a", "b"), ("c", "d"), ("e", "f"))
    assert column_consistency(uniform) == 1.0


def test_column_consistency_penalises_ragged_rows() -> None:
    ragged = (("a", "b"), ("c",), ("d", "e", "f"))
    assert column_consistency(ragged) < 0.5


def test_fill_rate_counts_empty_cells() -> None:
    assert fill_rate((("a", ""), ("b", ""))) == 0.5


def test_type_coherence_prefers_single_typed_columns() -> None:
    coherent = _table([["h1", "h2"], ["$1.00", "2020-01-01"], ["$2.00", "2020-02-01"]])
    mixed = _table([["h1", "h2"], ["$1.00", "abc"], ["xyz", "2020-02-01"]])

    assert type_coherence(coherent.rows) > type_coherence(mixed.rows)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("$1,234.56", "currency"),
        ("1,234.56", "currency"),
        ("(500.00)", "currency"),
        ("10/02/2023", "date"),
        ("2023-10-02", "date"),
        ("42", "integer"),
        ("01-038036", "identifier"),
        ("GANTT THOMAS", "text"),
        ("", "empty"),
    ],
)
def test_infer_kind(value: str, expected: str) -> None:
    assert infer_kind(value) == expected


def test_header_row_scores_above_data_row() -> None:
    header = ("PARCEL", "OWNER", "LOCATION", "ASSESSMENT")
    data = ("01-008153", "GANTT THOMAS", "8.5 AC", "133,000")

    assert row_header_score(header) > row_header_score(data)


def test_single_cell_row_is_not_a_header() -> None:
    assert row_header_score(("2026 CALVERT COUNTY TAX SALE RESULTS", "", "", "")) == 0.0


def test_score_table_is_bounded() -> None:
    score, signals = score_table(_table([["a", "b"], ["1", "2"]]))

    assert 0.0 <= score <= 1.0
    assert set(signals) == {
        "column_consistency",
        "fill_rate",
        "type_coherence",
        "header_plausibility",
        "size",
    }


def test_empty_table_scores_zero() -> None:
    score, _ = score_table(_table([]))
    assert score == 0.0


# --------------------------------------------------------------------------------------
# header detection
# --------------------------------------------------------------------------------------


def test_header_found_below_title_rows() -> None:
    table = _table(
        [
            ["ANNUAL TAX SALE", "", "", ""],
            ["MAY 22, 2026", "", "", ""],
            ["PARCEL", "OWNER", "LOCATION", "AMOUNT"],
            ["01-1", "SMITH JOHN", "1 MAIN ST", "1,000.00"],
        ]
    )

    detection = HeaderReconstructor().detect(table)

    assert detection.header_row_index == 2
    assert detection.headers == ("PARCEL", "OWNER", "LOCATION", "AMOUNT")


def test_header_detection_raises_on_empty_table() -> None:
    with pytest.raises(HeaderDetectionError):
        HeaderReconstructor().detect(_table([]))


def test_header_detection_raises_when_nothing_is_plausible() -> None:
    table = _table([["$1.00", "$2.00"], ["$3.00", "$4.00"]])

    with pytest.raises(HeaderDetectionError):
        HeaderReconstructor(min_header_score=0.95).detect(table)


def test_parser_errors_are_app_errors() -> None:
    assert issubclass(ParserError, AppError)
    assert issubclass(HeaderDetectionError, ParserError)


# --------------------------------------------------------------------------------------
# duplicate and blank header handling
# --------------------------------------------------------------------------------------


def test_duplicate_headers_get_positional_suffixes() -> None:
    headers, changed = disambiguate(("Amount", "Amount", "Amount"))

    assert headers == ("Amount", "Amount__2", "Amount__3")
    assert changed == ("Amount__2", "Amount__3")


def test_blank_headers_become_positional_placeholders() -> None:
    headers, changed = disambiguate(("Parcel", "", "Owner"))

    assert headers == ("Parcel", "column_2", "Owner")
    assert "column_2" in changed


def test_duplicate_detection_ignores_case() -> None:
    headers, _ = disambiguate(("Total", "TOTAL"))

    assert headers == ("Total", "TOTAL__2")


def test_disambiguation_never_loses_a_column() -> None:
    original = ("A", "A", "", "B", "")
    headers, _ = disambiguate(original)

    assert len(headers) == len(original)
    assert len(set(headers)) == len(original)


# --------------------------------------------------------------------------------------
# row/header pairing
# --------------------------------------------------------------------------------------


def test_short_rows_are_padded_not_dropped() -> None:
    values = pair_cells_with_headers(("A", "B", "C"), ("1", "2"))

    assert values == {"A": "1", "B": "2", "C": ""}


def test_extra_cells_are_kept_as_overflow() -> None:
    values = pair_cells_with_headers(("A", "B"), ("1", "2", "3"))

    assert values["A"] == "1"
    assert values["B"] == "2"
    assert values["__overflow_3"] == "3"


def test_pairing_preserves_every_input_cell() -> None:
    cells = ("1", "2", "3", "4")
    values = pair_cells_with_headers(("A", "B"), cells)

    assert sorted(values.values()) == sorted(cells)


# --------------------------------------------------------------------------------------
# stitching
# --------------------------------------------------------------------------------------


def test_repeated_header_page_is_stripped() -> None:
    page1 = _table([["A", "B"], ["1", "2"]], page=1)
    page2 = _table([["A", "B"], ["3", "4"]], page=2)

    stitched = TableStitcher().stitch([page1, page2])

    assert len(stitched) == 1
    assert [row for _, _, row in stitched[0].rows] == [("1", "2"), ("3", "4")]
    assert stitched[0].repeated_header_pages == [2]


def test_headerless_continuation_keeps_all_its_rows() -> None:
    page1 = _table([["PARCEL", "OWNER"], ["01-1", "SMITH JOHN"]], page=1)
    page2 = _table([["02-2", "JONES MARY"], ["03-3", "BROWN LEE"]], page=2)

    stitched = TableStitcher().stitch([page1, page2])

    assert len(stitched) == 1
    rows = [row for _, _, row in stitched[0].rows]
    assert ("02-2", "JONES MARY") in rows
    assert ("03-3", "BROWN LEE") in rows
    assert len(rows) == 3


def test_different_column_count_starts_a_new_table() -> None:
    page1 = _table([["A", "B"], ["1", "2"]], page=1)
    page2 = _table([["X", "Y", "Z"], ["7", "8", "9"]], page=2)

    stitched = TableStitcher().stitch([page1, page2])

    assert len(stitched) == 2


def test_stitching_preserves_true_page_numbers() -> None:
    page1 = _table([["A", "B"], ["1", "2"]], page=1)
    page2 = _table([["3", "4"]], page=2)

    stitched = TableStitcher().stitch([page1, page2])
    pages = {page for page, _, _ in stitched[0].rows}

    assert pages == {1, 2}


def test_headerless_continuation_reuses_prior_headers_and_keeps_first_data_row() -> None:
    page1 = _table(
        [
            ["Tax Deed Number", "Sale Date", "Balance"],
            ["2014001930", "9/30/2014", "508.74"],
        ],
        page=1,
    )
    page2 = _table(
        [
            ["2014000702", "10/7/2014", "1,768.73"],
            ["2014001622", "1/13/2015", "1,328.92"],
        ],
        page=2,
    )

    stitched = TableStitcher().stitch([page1, page2])

    assert len(stitched) == 1
    assert stitched[0].header.headers == ("Tax Deed Number", "Sale Date", "Balance")
    rows = [row for _, _, row in stitched[0].rows]
    assert rows[0] == ("2014001930", "9/30/2014", "508.74")
    assert rows[1] == ("2014000702", "10/7/2014", "1,768.73")
    assert rows[2] == ("2014001622", "1/13/2015", "1,328.92")


def test_incompatible_data_page_does_not_inherit_or_steal_parent() -> None:
    """A wrong-width data page must not become the continuation parent."""
    page1 = _table(
        [
            ["Tax Deed Number", "Sale Date", "Balance", "Owner Name"],
            ["2014001930", "9/30/2014", "508.74", "SMITH JOHN"],
        ],
        page=1,
    )
    page2 = _table(
        [
            [
                "2022001757",
                "2/14/2023 22,843.06",
                "11/20/2024",
                "23 NW 18TH PL CAPE CORAL FL 33993",
                "09-44-23-C3-03725.0060 LYL 509 LLC",
                "6/30/2023",
            ]
        ],
        page=2,
    )
    page3 = _table(
        [
            ["2024000125", "9/10/2024", "32,303.49", "CJC 431 ST LLC"],
            ["2024000072", "9/10/2024", "8,294.58", "SMITH JANE"],
        ],
        page=3,
    )

    stitched = TableStitcher().stitch([page1, page2, page3])

    parent = next(t for t in stitched if t.header.headers[0] == "Tax Deed Number")
    parent_rows = [row for _, _, row in parent.rows]
    assert ("2014001930", "9/30/2014", "508.74", "SMITH JOHN") in parent_rows
    assert ("2024000125", "9/10/2024", "32,303.49", "CJC 431 ST LLC") in parent_rows
    assert ("2024000072", "9/10/2024", "8,294.58", "SMITH JANE") in parent_rows
    assert 1 in parent.page_numbers
    assert 3 in parent.page_numbers
    isolated = [t for t in stitched if t is not parent]
    assert isolated
    assert isolated[0].header.headers[0] != "Tax Deed Number"
    assert isolated[0].header.headers != page1.rows[0]


def test_later_table_with_own_valid_header_is_not_merged() -> None:
    page1 = _table([["Parcel", "Owner"], ["01-1", "SMITH JOHN"]], page=1)
    page2 = _table([["Account", "Taxpayer"], ["99-9", "JONES MARY"]], page=2)

    stitched = TableStitcher().stitch([page1, page2])

    assert len(stitched) == 2
    assert stitched[0].header.headers == ("Parcel", "Owner")
    assert stitched[1].header.headers == ("Account", "Taxpayer")
    assert [row for _, _, row in stitched[1].rows] == [("99-9", "JONES MARY")]


# --------------------------------------------------------------------------------------
# word clustering
# --------------------------------------------------------------------------------------


def test_words_group_into_lines_by_vertical_position() -> None:
    words = [
        _word("a", 0, 10, 100),
        _word("b", 20, 30, 101),
        _word("c", 0, 10, 140),
    ]

    lines = group_words_into_lines(words, tolerance=3.0)

    assert len(lines) == 2
    assert [w["text"] for w in lines[0]] == ["a", "b"]


def test_lines_are_ordered_left_to_right() -> None:
    words = [_word("right", 100, 120, 50), _word("left", 10, 30, 50)]

    lines = group_words_into_lines(words, tolerance=3.0)

    assert [w["text"] for w in lines[0]] == ["left", "right"]


def test_column_boundaries_found_in_whitespace_gutters() -> None:
    words = [
        _word("aaa", 0, 30, 10),
        _word("bbb", 100, 130, 10),
        _word("ccc", 0, 30, 30),
        _word("ddd", 100, 130, 30),
    ]

    boundaries = find_column_boundaries(words, min_gap_width=6.0)

    assert len(boundaries) == 1
    assert 30 < boundaries[0] < 100


def test_no_boundaries_when_text_is_contiguous() -> None:
    words = [_word("aaaa", 0, 50, 10), _word("bbbb", 51, 100, 10)]

    assert find_column_boundaries(words, min_gap_width=6.0) == []


def test_column_index_assignment() -> None:
    boundaries = [50.0, 100.0]

    assert column_index_for(10.0, boundaries) == 0
    assert column_index_for(75.0, boundaries) == 1
    assert column_index_for(150.0, boundaries) == 2


def test_find_column_boundaries_handles_no_words() -> None:
    assert find_column_boundaries([], min_gap_width=6.0) == []


# --------------------------------------------------------------------------------------
# continuation-page column geometry
# --------------------------------------------------------------------------------------

# Eight logical bands. Neighbouring values can sit closer than min_gap_width and still
# fall on opposite sides of a prior gutter.
_GEOM_BOXES = (
    (10.0, 40.0),
    (60.0, 80.0),
    (100.0, 120.0),
    (140.0, 160.0),
    (200.0, 280.0),
    (330.0, 390.0),
    (430.0, 530.0),
    (570.0, 650.0),
)
_HEADER_8 = (
    "Tax Deed Number",
    "Sale Date",
    "Balance",
    "Balance Date",
    "Property Address",
    "Parcel ID",
    "Owner Name",
    "Expires",
)
_DATA_A = (
    "2014001930",
    "9/30/2014",
    "508.74",
    "6/26/2019",
    "133 NAVAHO AVE",
    "01452704000230050",
    "TAHIR S ANSARI",
    "1/1/2020",
)
_DATA_B = (
    "2014000702",
    "10/7/2014",
    "1768.73",
    "6/26/2019",
    "ACCESS UNDETERMINED",
    "09452704000160170",
    "JONES MARY",
    "2/2/2021",
)
_DATA_C = (
    "2014001622",
    "1/13/2015",
    "1328.92",
    "7/1/2019",
    "2506 51ST ST",
    "13452607000058014",
    "SMITH JOHN",
    "3/3/2022",
)


def _cells_at(top: float, texts: tuple[str, ...], boxes: tuple[tuple[float, float], ...] = _GEOM_BOXES) -> list[dict[str, float | str]]:
    return [_word(text, x0, x1, top) for text, (x0, x1) in zip(texts, boxes, strict=True)]


def _merge_boxes(left: int, right: int) -> tuple[tuple[float, float], ...]:
    """Close the gutter between two adjacent logical columns so native clustering merges them."""
    boxes = list(_GEOM_BOXES)
    left_x0, _left_x1 = boxes[left]
    _right_x0, right_x1 = boxes[right]
    # One-point gap: narrower than DEFAULT_MIN_GAP_WIDTH, so it is not a native gutter.
    mid = (boxes[left][1] + boxes[right][0]) / 2
    boxes[left] = (left_x0, mid - 0.4)
    boxes[right] = (mid + 0.4, right_x1)
    return tuple(boxes)


def test_header_page_establishes_eight_native_columns() -> None:
    words = (
        _cells_at(10, _HEADER_8)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )
    tables = extract_tables_from_word_pages([(1, words)])

    assert len(tables) == 1
    assert tables[0].column_count == 8
    assert tables[0].rows[0] == _HEADER_8


def test_continuation_recovers_eight_columns_when_native_merges_sale_and_balance() -> None:
    """Logical columns 2+3 (Sale Date, Balance) sit closer than a native gutter."""
    header_page = (
        _cells_at(10, _HEADER_8)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )
    merged = _merge_boxes(1, 2)
    continuation = (
        _cells_at(10, _DATA_B, merged)
        + _cells_at(30, _DATA_C, merged)
        + _cells_at(50, _DATA_A, merged)
    )
    native_bounds = find_column_boundaries(continuation, min_gap_width=6.0)
    assert len(native_bounds) + 1 == 7

    tables = extract_tables_from_word_pages([(1, header_page), (2, continuation)])

    assert len(tables) == 2
    assert tables[1].column_count == 8
    assert tables[1].rows[0] == _DATA_B
    assert tables[1].rows[0][1] == "10/7/2014"
    assert tables[1].rows[0][2] == "1768.73"


def test_continuation_recovers_eight_columns_when_native_merges_parcel_and_owner() -> None:
    """Logical columns 6+7 (Parcel ID, Owner Name) sit closer than a native gutter."""
    header_page = (
        _cells_at(10, _HEADER_8)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )
    merged = _merge_boxes(5, 6)
    continuation = (
        _cells_at(10, _DATA_C, merged)
        + _cells_at(30, _DATA_A, merged)
        + _cells_at(50, _DATA_B, merged)
    )
    native_bounds = find_column_boundaries(continuation, min_gap_width=6.0)
    assert len(native_bounds) + 1 == 7

    tables = extract_tables_from_word_pages([(1, header_page), (2, continuation)])

    assert tables[1].column_count == 8
    assert tables[1].rows[0] == _DATA_C
    assert tables[1].rows[0][5] == "13452607000058014"
    assert tables[1].rows[0][6] == "SMITH JOHN"


def test_continuation_first_row_is_data_not_a_header() -> None:
    header_page = (
        _cells_at(10, _HEADER_8)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )
    continuation = _cells_at(10, _DATA_B) + _cells_at(30, _DATA_C) + _cells_at(50, _DATA_A)

    tables = extract_tables_from_word_pages([(1, header_page), (2, continuation)])
    stitched = TableStitcher().stitch(tables)

    assert len(stitched) == 1
    rows = [row for _, _, row in stitched[0].rows]
    assert rows[0] == _DATA_A
    assert rows[1] == _DATA_B
    assert _DATA_B in rows
    assert stitched[0].header.headers[0] == "Tax Deed Number"


def test_prior_geometry_is_not_reused_when_x_alignment_is_weak() -> None:
    header_page = (
        _cells_at(10, _HEADER_8)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )
    # Three lines of a different two-column layout, far from the prior bands.
    shifted = (
        [_word("AA-1", 12, 40, 10), _word("SMITH", 80, 140, 10)]
        + [_word("AA-2", 12, 40, 30), _word("JONES", 80, 140, 30)]
        + [_word("AA-3", 12, 40, 50), _word("BROWN", 80, 140, 50)]
        + [_word("AA-4", 12, 40, 70), _word("WHITE", 80, 140, 70)]
    )
    prior_bounds = find_column_boundaries(header_page, min_gap_width=6.0)
    assert geometry_alignment_score(shifted, prior_bounds) < 0.85

    tables = extract_tables_from_word_pages([(1, header_page), (2, shifted)])

    assert tables[1].column_count != 8
    assert tables[1].column_count == 2


def test_prior_geometry_is_not_reused_for_a_new_labeled_table() -> None:
    header_page = (
        _cells_at(10, _HEADER_8)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )
    other_headers = (
        "Account Number",
        "Taxpayer Name",
        "Mailing City",
        "Mailing State",
        "Site Street",
        "Folio Number",
        "Legal Owner",
        "Filed Date",
    )
    other_page = (
        _cells_at(10, other_headers)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )

    tables = extract_tables_from_word_pages([(1, header_page), (2, other_page)])
    stitched = TableStitcher().stitch(tables)

    assert len(stitched) == 2
    assert stitched[0].header.headers[0] == "Tax Deed Number"
    assert stitched[1].header.headers[0] == "Account Number"
    assert tables[1].rows[0] == other_headers


def test_geometry_reuse_does_not_depend_on_page_number() -> None:
    header_page = (
        _cells_at(10, _HEADER_8)
        + _cells_at(30, _DATA_A)
        + _cells_at(50, _DATA_B)
        + _cells_at(70, _DATA_C)
    )
    merged = _merge_boxes(1, 2)
    continuation = (
        _cells_at(10, _DATA_B, merged)
        + _cells_at(30, _DATA_C, merged)
        + _cells_at(50, _DATA_A, merged)
    )

    tables = extract_tables_from_word_pages([(20, header_page), (21, continuation)])

    assert [t.page_number for t in tables] == [20, 21]
    assert tables[1].column_count == 8
    assert tables[1].rows[0] == _DATA_B


def test_rows_from_words_keep_verbatim_cell_text() -> None:
    words = _cells_at(10, _DATA_A) + _cells_at(30, _DATA_B) + _cells_at(50, _DATA_C)
    bounds = find_column_boundaries(words, min_gap_width=6.0)
    rows = rows_from_words(words, bounds)

    assert rows[0][0] == "2014001930"
    assert rows[0][2] == "508.74"


# --------------------------------------------------------------------------------------
# model invariants
# --------------------------------------------------------------------------------------


def test_extracted_table_reports_widest_row() -> None:
    table = _table([["a"], ["b", "c", "d"]])

    assert table.column_count == 3
    assert table.row_count == 2


def test_extraction_method_enum_covers_strategies() -> None:
    assert ExtractionMethod.TABLE.value == "table"
    assert ExtractionMethod.OCR.value == "ocr"
