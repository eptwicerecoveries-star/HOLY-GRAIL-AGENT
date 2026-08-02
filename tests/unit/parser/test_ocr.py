"""OCR extraction, and the profile learned from a real document.

These run only where the OCR toolchain is installed, and skip cleanly otherwise, so the
suite stays usable on a machine without tesseract.
"""

from __future__ import annotations

import pytest

from surplus_ai.database.models.enums import ExtractionMethod, PdfType
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.models import (
    InterpretedDocument,
    RoutingDecision,
    SurplusSource,
)
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.models import ParsedDocumentResult
from surplus_ai.parser.pipeline import ParsingPipeline
from surplus_ai.parser.profiles.learner import ProfileLearner
from surplus_ai.parser.strategies.ocr_table import (
    OcrTableStrategy,
    merge_continuation_rows,
    merge_header_fragments,
)
from tests.unit.parser.conftest import CORPUS_DIR

pytestmark = pytest.mark.skipif(
    not OcrTableStrategy.is_available(),
    reason="the OCR toolchain (tesseract, poppler) is not installed",
)


@pytest.fixture(scope="module")
def scanned() -> ParsedDocumentResult:
    return ParsingPipeline().parse(CORPUS_DIR / "St_Marys_County_MD.pdf")


@pytest.fixture(scope="module")
def scanned_interpreted(scanned: ParsedDocumentResult) -> InterpretedDocument:
    return InterpretationPipeline().interpret(scanned, None)


# --------------------------------------------------------------------------------------
# Reading a table that exists only as pixels
# --------------------------------------------------------------------------------------


def test_scanned_document_is_read_by_ocr(scanned: ParsedDocumentResult) -> None:
    assert scanned.winning_strategy == "ocr_table"
    assert scanned.profile.pdf_type is PdfType.HYBRID
    assert scanned.total_rows > 20


def test_ocr_recovers_the_published_headers(scanned: ParsedDocumentResult) -> None:
    """The header is stacked three deep in the image and must come back as one row."""
    headers = " ".join(scanned.tables[0].original_headers).casefold()

    for expected in ("parcel", "tax sale", "foreclosed", "owner name", "claim"):
        assert expected in headers


def test_ocr_rows_carry_the_ocr_method(scanned: ParsedDocumentResult) -> None:
    for row in scanned.tables[0].rows:
        assert row.extraction_method is ExtractionMethod.OCR
        assert row.extraction_strategy == "ocr_table"


def test_ocr_columns_resolve_to_canonical_fields(
    scanned_interpreted: InterpretedDocument,
) -> None:
    fields = set(scanned_interpreted.tables[0].resolved_fields)

    assert CanonicalField.PARCEL_ID in fields
    assert CanonicalField.OWNER_NAME in fields
    assert CanonicalField.CLAIM_DEADLINE in fields


def test_scanned_county_publishes_no_amount(
    scanned_interpreted: InterpretedDocument,
) -> None:
    """The page is titled "Balance of Bids/Excess Funds" but publishes no figure per row.

    Naming a document after surplus is not the same as publishing one, so the correct
    outcome is no amount at all rather than an amount borrowed from the title.
    """
    table = scanned_interpreted.tables[0]

    assert table.surplus.source is SurplusSource.ABSENT
    assert scanned_interpreted.rows_with_surplus == 0


def test_ocr_rows_never_auto_accept(scanned_interpreted: InterpretedDocument) -> None:
    """The hard cap, exercised end to end on a real scanned document."""
    counts = scanned_interpreted.routing_counts()

    assert counts[RoutingDecision.AUTO_ACCEPT] == 0
    assert counts[RoutingDecision.REVIEW] == scanned_interpreted.total_rows


def test_ocr_did_not_disturb_the_text_layer_counties() -> None:
    """Adding a fourth strategy must not change what the other counties extract."""
    pipeline = ParsingPipeline()

    assert pipeline.parse(CORPUS_DIR / "Calvert_County_MD.pdf").total_rows == 96
    assert pipeline.parse(CORPUS_DIR / "Harford_County_MD.pdf").total_rows == 49


# --------------------------------------------------------------------------------------
# Row and header repair
# --------------------------------------------------------------------------------------


def test_wrapped_line_folds_into_its_record() -> None:
    rows = [("01-1", "3/1/2020", ""), ("", "", "SMITH JOHN")]

    assert merge_continuation_rows(rows) == [("01-1", "3/1/2020", "SMITH JOHN")]


def test_a_line_starting_a_new_record_is_not_folded() -> None:
    rows = [("01-1", "3/1/2020", ""), ("02-2", "", "SMITH JOHN")]

    assert len(merge_continuation_rows(rows)) == 2


def test_a_line_that_would_overwrite_is_not_folded() -> None:
    """Disjointness matters: a continuation adds, it never replaces."""
    rows = [("01-1", "3/1/2020", "JONES"), ("", "4/1/2020", "SMITH")]

    assert len(merge_continuation_rows(rows)) == 2


def test_stacked_header_fragments_are_rejoined() -> None:
    rows = [("PARCEL", "TAX SALE"), ("NUMBER", "DATE"), ("01-1", "3/1/2020")]

    merged = merge_header_fragments(rows)

    assert merged[0] == ("PARCEL NUMBER", "TAX SALE DATE")
    assert merged[1] == ("01-1", "3/1/2020")


def test_a_spanning_title_is_left_above_the_header() -> None:
    """A title is one long phrase; length is what separates it from column labels."""
    rows = [
        ("", "List of outstanding payments due from Balance of Bids"),
        ("PARCEL", "TAX SALE"),
        ("NUMBER", "DATE"),
        ("01-1", "3/1/2020"),
    ]

    merged = merge_header_fragments(rows)

    assert "List of outstanding" in merged[0][1]
    assert merged[1] == ("PARCEL NUMBER", "TAX SALE DATE")


def test_a_single_header_line_is_untouched() -> None:
    rows = [("PARCEL", "OWNER"), ("01-1", "SMITH")]

    assert merge_header_fragments(rows) == rows


def test_header_merge_handles_empty_input() -> None:
    assert merge_header_fragments([]) == []
    assert merge_continuation_rows([]) == []


# --------------------------------------------------------------------------------------
# The profile learned from a scanned county
# --------------------------------------------------------------------------------------


def test_scanned_profile_records_that_ocr_was_needed(
    scanned: ParsedDocumentResult, scanned_interpreted: InterpretedDocument
) -> None:
    profile = ProfileLearner().learn("st-marys", scanned, scanned_interpreted, state="MD")

    assert profile.ocr_required is True
    assert profile.pdf_type is PdfType.HYBRID
    assert profile.required_parsing_strategy == "ocr_table"
    assert profile.surplus_explicitly_listed is False
    assert len(profile.original_column_names) >= 4


def test_scanned_profile_observes_owner_shapes(
    scanned: ParsedDocumentResult, scanned_interpreted: InterpretedDocument
) -> None:
    profile = ProfileLearner().learn("st-marys", scanned, scanned_interpreted, state="MD")

    assert profile.owner_types.sampled > 0
    assert profile.owner_types.person_like + profile.owner_types.entity_like == (
        profile.owner_types.sampled
    )
