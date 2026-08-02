"""Corpus-driven tests.

Every PDF in `data/test_pdfs/` is parametrized automatically. A new county file is picked
up with no code change; if it also has a golden file in `expected/`, its structure and
contents are asserted too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from surplus_ai.database.models.enums import PdfType
from surplus_ai.parser.document_classifier import DocumentClassifier
from surplus_ai.parser.exceptions import OCRRequiredError, ParserError
from surplus_ai.parser.models import ParsedDocumentResult
from surplus_ai.parser.pipeline import ParsingPipeline
from tests.unit.parser.conftest import (
    corpus_ids,
    corpus_pdfs,
    expectations_for,
    pdfs_with_expectations,
)

CORPUS = corpus_pdfs()
CORPUS_IDS = corpus_ids()
WITH_EXPECTATIONS = pdfs_with_expectations()
WITH_EXPECTATIONS_IDS = [p.stem for p in WITH_EXPECTATIONS]


@pytest.fixture(scope="module")
def classifier() -> DocumentClassifier:
    return DocumentClassifier()


@pytest.fixture(scope="module")
def pipeline() -> ParsingPipeline:
    return ParsingPipeline()


@pytest.fixture(scope="module")
def parsed(pipeline: ParsingPipeline) -> dict[str, ParsedDocumentResult]:
    """Parse each corpus PDF once; the largest file is 55 pages."""
    results: dict[str, ParsedDocumentResult] = {}
    for pdf in CORPUS:
        expectations = expectations_for(pdf) or {}
        if expectations.get("parse_raises"):
            continue
        try:
            results[pdf.stem] = pipeline.parse(pdf)
        except ParserError:
            continue
    return results


def test_corpus_is_not_empty() -> None:
    assert CORPUS, "No PDFs found in data/test_pdfs/"


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("pdf", CORPUS, ids=CORPUS_IDS)
def test_every_pdf_classifies(pdf: Path, classifier: DocumentClassifier) -> None:
    profile = classifier.classify(pdf)

    assert profile.page_count > 0
    assert len(profile.pages) == profile.page_count
    assert profile.source_sha256
    assert 0.0 <= profile.confidence <= 1.0


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_classification_matches_expectations(pdf: Path, classifier: DocumentClassifier) -> None:
    expected = expectations_for(pdf)
    assert expected is not None
    profile = classifier.classify(pdf)

    assert profile.pdf_type is PdfType(expected["pdf_type"])
    assert profile.ocr_required is expected["ocr_required"]
    assert profile.page_count == expected["page_count"]
    if "ocr_required_pages" in expected:
        assert list(profile.ocr_required_pages) == expected["ocr_required_pages"]


def test_page_text_alone_does_not_imply_searchable(classifier: DocumentClassifier) -> None:
    """St. Mary's is the reason classification is per region rather than per document.

    The page has a substantial text layer, yet the table is a raster image. A
    document-level "has text?" test would call this searchable and silently yield nothing.
    """
    pdf = _corpus_file("St_Marys_County_MD")
    expected = expectations_for(pdf)
    assert expected is not None

    profile = classifier.classify(pdf)
    page = profile.pages[0]

    assert page.char_count == expected["page_char_count"]
    assert page.char_count > 1000, "precondition: the page really does carry lots of text"
    assert profile.ocr_required is True
    assert profile.pdf_type is PdfType.HYBRID

    data_regions = [r for r in page.image_regions if r.is_data_region]
    assert data_regions, "the large image region must be recognised as holding data"
    assert data_regions[0].area_ratio >= expected["data_region_area_ratio_min"]
    assert data_regions[0].chars_inside <= expected["data_region_chars_inside_max"]


def test_stray_glyph_inside_an_image_does_not_mask_it(
    classifier: DocumentClassifier,
) -> None:
    """A single character inside a scanned table must not make it look like text.

    Testing for exactly zero characters is what an obvious implementation would do, and
    St. Mary's contains precisely one stray glyph inside its table image.
    """
    profile = classifier.classify(_corpus_file("St_Marys_County_MD"))
    region = next(r for r in profile.pages[0].image_regions if r.is_data_region)

    assert region.chars_inside > 0
    assert region.text_density < 0.5


# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_parse_outcome_matches_expectations(
    pdf: Path, pipeline: ParsingPipeline, parsed: dict[str, ParsedDocumentResult]
) -> None:
    expected = expectations_for(pdf)
    assert expected is not None

    if expected.get("parse_raises") == "OCRRequiredError":
        with pytest.raises(OCRRequiredError):
            pipeline.parse(pdf)
        return

    result = parsed[pdf.stem]
    assert len(result.tables) == expected["table_count"]
    if "row_count" in expected:
        assert result.total_rows == expected["row_count"]
    if "min_row_count" in expected:
        # Recognised documents assert a floor rather than an exact count: the number of
        # rows a scanned page yields shifts slightly with the Tesseract build, and pinning
        # it exactly would make the suite fail on a different machine rather than on a
        # real regression.
        assert result.total_rows >= expected["min_row_count"]
    if "expected_strategy" in expected:
        assert result.winning_strategy == expected["expected_strategy"]
    if "expected_header_tokens" in expected:
        headers = " ".join(h for table in result.tables for h in table.original_headers).casefold()
        for token in expected["expected_header_tokens"]:
            assert token in headers


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_headers_are_preserved_verbatim(pdf: Path, parsed: dict[str, ParsedDocumentResult]) -> None:
    expected = expectations_for(pdf)
    assert expected is not None
    if "expected_headers" not in expected:
        return

    result = parsed[pdf.stem]
    actual = [list(t.original_headers) for t in result.tables]
    assert actual == expected["expected_headers"]


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_spot_check_rows_match(pdf: Path, parsed: dict[str, ParsedDocumentResult]) -> None:
    expected = expectations_for(pdf)
    assert expected is not None
    if "spot_check_rows" not in expected:
        return

    rows = [row for table in parsed[pdf.stem].tables for row in table.rows]
    for spot in expected["spot_check_rows"]:
        match = next(
            (
                r
                for r in rows
                if r.page_number == spot["page_number"]
                and all(r.values.get(k) == v for k, v in spot["values"].items())
            ),
            None,
        )
        assert match is not None, f"no row in {pdf.stem} matched {spot['values']}"


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_nothing_is_discarded(pdf: Path, parsed: dict[str, ParsedDocumentResult]) -> None:
    """Text that is not a data row still has to survive as an unparsed fragment."""
    expected = expectations_for(pdf)
    assert expected is not None
    if pdf.stem not in parsed or "expected_fragments_contain" not in expected:
        return

    joined = "\n".join(parsed[pdf.stem].unparsed_fragments)
    for needle in expected["expected_fragments_contain"]:
        assert needle in joined, f"{needle!r} was dropped from {pdf.stem}"


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_row_count_matches_the_documents_own_declared_total(
    pdf: Path, parsed: dict[str, ParsedDocumentResult]
) -> None:
    """Where a county states its own record count, extraction must reproduce it exactly.

    This is the strongest available check on row recall: it comes from the publisher, not
    from us, and it catches both dropped rows and repeated header rows counted as data.
    """
    expected = expectations_for(pdf)
    assert expected is not None
    declared = expected.get("declared_row_count")
    if declared is None:
        return

    assert parsed[pdf.stem].total_rows == declared


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_every_row_carries_provenance(pdf: Path, parsed: dict[str, ParsedDocumentResult]) -> None:
    if pdf.stem not in parsed:
        return
    result = parsed[pdf.stem]
    valid_pages = set(range(1, result.profile.page_count + 1))

    for table in result.tables:
        for row in table.rows:
            assert row.page_number in valid_pages
            assert row.source_pdf_sha256 == result.profile.source_sha256
            assert row.source_pdf_path == result.profile.source_path
            assert row.extraction_strategy == result.winning_strategy
            assert 0.0 <= row.confidence <= 1.0


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_rows_share_the_header_key_set(pdf: Path, parsed: dict[str, ParsedDocumentResult]) -> None:
    if pdf.stem not in parsed:
        return
    for table in parsed[pdf.stem].tables:
        headers = set(table.original_headers)
        for row in table.rows:
            assert headers <= set(row.values), "a row lost one of the published columns"


# --------------------------------------------------------------------------------------
# Layout behaviours that differ across counties
# --------------------------------------------------------------------------------------


def test_headerless_continuation_page_inherits_headers(
    parsed: dict[str, ParsedDocumentResult],
) -> None:
    """Calvert prints its header once; page 2 continues without one.

    The failure this guards against is subtle: header detection will nominate the first
    data row of page 2, which would both split the table and consume a real record.
    """
    result = parsed["Calvert_County_MD"]

    assert len(result.tables) == 1
    table = result.tables[0]
    assert list(table.page_numbers) == [1, 2]
    assert "PARCEL" in table.original_headers
    assert any(r.page_number == 2 for r in table.rows)

    page_two_first = next(r for r in table.rows if r.page_number == 2)
    assert page_two_first.values["PARCEL"] == "03-021246"


def test_repeated_header_pages_are_not_counted_as_data(
    parsed: dict[str, ParsedDocumentResult],
) -> None:
    """Marion repeats its header on all 49 pages; those repeats are not records."""
    result = parsed["Marion_County_IN_2023"]
    table = result.tables[0]

    assert result.profile.page_count == 49
    assert table.row_count == 950
    assert not any(r.values.get("Unique #") == "Unique #" for r in table.rows)


def test_wrapped_headers_are_rejoined_in_published_order(
    parsed: dict[str, ParsedDocumentResult],
) -> None:
    """Marion's headers wrap over two lines and must rejoin by position, not reading order.

    The linearized text invites pairing `Amount` with `Face Value`; the word boxes put it
    under `Purchase`, and the arithmetic agrees.
    """
    headers = parsed["Marion_County_IN_2023"].tables[0].original_headers

    assert "Purchase Amount" in headers
    assert "Refunded Overbid" in headers
    assert "Remaining Overbid" in headers
    assert "Face Value" in headers
    assert "Face Value Amount" not in headers


def test_marion_money_columns_stay_distinct(
    parsed: dict[str, ParsedDocumentResult],
) -> None:
    """Three columns mention 'overbid'; collapsing them would destroy the distinction
    between money already refunded and money the county still holds."""
    table = parsed["Marion_County_IN_2023"].tables[0]
    overbid_columns = [h for h in table.original_headers if "overbid" in h.casefold()]

    assert sorted(overbid_columns) == ["Overbid", "Refunded Overbid", "Remaining Overbid"]

    row = table.rows[0]
    assert row.values["Overbid"] == "$3,203.00"
    assert row.values["Refunded Overbid"] == "$3,203.00"
    assert row.values["Remaining Overbid"] == "$0.00"


def test_header_row_is_not_assumed_to_be_row_zero(
    parsed: dict[str, ParsedDocumentResult],
) -> None:
    """Calvert's first two rows are a title and a sale date, not column labels."""
    result = parsed["Calvert_County_MD"]
    fragments = "\n".join(result.unparsed_fragments)

    assert result.tables[0].original_headers[0] == "PARCEL"
    assert "2026 CALVERT COUNTY TAX SALE RESULTS" in fragments
    assert "FRIDAY, MAY 22, 2026" in fragments


def test_calvert_publishes_no_surplus_column(
    parsed: dict[str, ParsedDocumentResult],
) -> None:
    """Bid exceeds sale amount, so a surplus exists arithmetically, but none is published.

    Phase 2B must therefore record no surplus for this county rather than deriving one.
    """
    headers = parsed["Calvert_County_MD"].tables[0].original_headers

    assert not any("surplus" in h.casefold() for h in headers)
    assert not any("excess" in h.casefold() for h in headers)
    assert {"ASSESSMENT", "SALE AMOUNT", "BID AMOUNT"} <= set(headers)

    row = parsed["Calvert_County_MD"].tables[0].rows[0]
    assert row.values["BID AMOUNT"] == "15,000.00"
    assert row.values["SALE AMOUNT"] == "3,743.93"


def test_harford_publishes_an_explicit_surplus_column(
    parsed: dict[str, ParsedDocumentResult],
) -> None:
    headers = parsed["Harford_County_MD"].tables[0].original_headers

    assert "SURPLUS" in headers
    assert parsed["Harford_County_MD"].tables[0].rows[0].values["SURPLUS"] == "$491.33"


# --------------------------------------------------------------------------------------
# Strategy selection
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_more_than_one_strategy_is_considered(
    pdf: Path, parsed: dict[str, ParsedDocumentResult]
) -> None:
    """The winner is always the highest scorer among the strategies that could run.

    A document whose data is an image legitimately offers only one candidate, since the
    text strategies cannot read it at all. Where a text layer exists, several strategies
    compete and the comparison is what picks between them.
    """
    if pdf.stem not in parsed:
        return
    result = parsed[pdf.stem]

    assert result.strategy_scores
    if result.profile.extractable_pages:
        assert len(result.strategy_scores) >= 2
    best = max(result.strategy_scores, key=lambda k: result.strategy_scores[k])
    assert result.winning_strategy == best


@pytest.mark.parametrize("pdf", WITH_EXPECTATIONS, ids=WITH_EXPECTATIONS_IDS)
def test_winning_strategy_is_confident(pdf: Path, parsed: dict[str, ParsedDocumentResult]) -> None:
    if pdf.stem not in parsed:
        return
    assert parsed[pdf.stem].extraction_confidence >= 0.8


def _corpus_file(stem: str) -> Path:
    match = next((p for p in CORPUS if p.stem == stem), None)
    if match is None:  # pragma: no cover - corpus guard
        pytest.skip(f"{stem}.pdf is not in the corpus")
    return match


def _unused(_: Any) -> None:  # pragma: no cover - keeps Any import meaningful
    return None
