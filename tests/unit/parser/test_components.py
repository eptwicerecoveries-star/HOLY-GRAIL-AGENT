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
    find_column_boundaries,
    group_words_into_lines,
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
# model invariants
# --------------------------------------------------------------------------------------


def test_extracted_table_reports_widest_row() -> None:
    table = _table([["a"], ["b", "c", "d"]])

    assert table.column_count == 3
    assert table.row_count == 2


def test_extraction_method_enum_covers_strategies() -> None:
    assert ExtractionMethod.TABLE.value == "table"
    assert ExtractionMethod.OCR.value == "ocr"
