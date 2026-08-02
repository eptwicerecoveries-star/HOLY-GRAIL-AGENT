from __future__ import annotations

import shutil
from pathlib import Path
from statistics import median
from typing import Any

import structlog

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.exceptions import StrategyExecutionError
from surplus_ai.parser.interpretation.type_inference import looks_like_date, looks_like_money
from surplus_ai.parser.models import DocumentProfile, ExtractedTable, PageProfile
from surplus_ai.parser.strategies.base import AbstractExtractionStrategy, normalize_cell
from surplus_ai.parser.strategies.word_cluster import (
    column_index_for,
    find_column_boundaries,
    group_words_into_lines,
)

logger = structlog.get_logger(__name__)

DEFAULT_DPI = 300
DEFAULT_MIN_WORD_CONFIDENCE = 30.0

# Tesseract's "assume a uniform block of text" mode. Chosen by measurement rather than
# preference: on the corpus's scanned table it recovers 202 words against 37 for the
# sparse-text modes, because the ruled table reads as one block rather than loose text.
DEFAULT_PSM = 6

# Line grouping and column gutters are measured in pixels here, not points, so both
# tolerances are derived from the rendered text height rather than hard-coded.
LINE_TOLERANCE_FACTOR = 0.6
MIN_GAP_FACTOR = 0.8

# Share of lines that must leave a position clear for it to count as a column gutter.
# Recognised word boxes are ragged and long owner names spill sideways, so demanding a
# perfectly empty column finds one gutter on the corpus's scanned county where there are
# four. Text layers keep the strict default.
DEFAULT_MIN_EMPTY_FRACTION = 0.85


class OcrTableStrategy(AbstractExtractionStrategy):
    """Reads tables that exist only as pixels.

    Runs only on pages the classifier flagged as needing OCR, and only inside the image
    regions that hold the data. Recognition happens on the whole page rather than on a
    crop: cropping deprives Tesseract of the surrounding layout and, on the corpus's
    scanned county, cuts word recovery from 202 words to 37. Words are filtered to the
    data region afterwards instead.

    Once words are recovered they are laid out by the same clustering the text strategies
    use, since a word box from Tesseract and a word box from a text layer carry the same
    information.

    Output from this strategy never auto-accepts downstream, whatever it scores. A misread
    digit in a money field is expensive and recognition confidence does not predict it
    well: on the corpus, parcel numbers that read cleanly at one setting came back as
    "fos-03a933" at another.
    """

    name = "ocr_table"
    extraction_method = ExtractionMethod.OCR

    def __init__(
        self,
        dpi: int = DEFAULT_DPI,
        psm: int = DEFAULT_PSM,
        min_word_confidence: float = DEFAULT_MIN_WORD_CONFIDENCE,
        min_empty_fraction: float = DEFAULT_MIN_EMPTY_FRACTION,
    ) -> None:
        self._dpi = dpi
        self._psm = psm
        self._min_confidence = min_word_confidence
        self._min_empty_fraction = min_empty_fraction

    @staticmethod
    def is_available() -> bool:
        """Whether the OCR toolchain is installed on this machine."""
        if shutil.which("tesseract") is None or shutil.which("pdftoppm") is None:
            return False
        try:
            import pdf2image  # noqa: F401
            import pytesseract  # noqa: F401
        except ImportError:
            return False
        return True

    def supports(self, profile: DocumentProfile) -> bool:
        return bool(profile.ocr_required_pages) and self.is_available()

    def extract(self, pdf_path: Path, profile: DocumentProfile) -> list[ExtractedTable]:
        pages = profile.ocr_required_pages
        if not pages:
            return []
        if not self.is_available():
            raise StrategyExecutionError(
                "OCR is required for this document but tesseract, poppler or their Python "
                "bindings are not installed"
            )

        try:
            images = self._render(pdf_path, pages)
        except Exception as exc:
            logger.warning("ocr_render_failed", path=str(pdf_path), error=str(exc))
            raise StrategyExecutionError(f"Could not rasterize {pdf_path}: {exc}") from exc

        tables: list[ExtractedTable] = []
        for page_number, image in images.items():
            page_profile = next(p for p in profile.pages if p.page_number == page_number)
            table = self._extract_page(image, page_profile)
            if table is not None:
                tables.append(table)
        logger.info(
            "ocr_extraction_complete",
            path=str(pdf_path),
            pages=list(pages),
            tables=len(tables),
            rows=sum(t.row_count for t in tables),
        )
        return tables

    def _render(self, pdf_path: Path, pages: tuple[int, ...]) -> dict[int, Any]:
        from pdf2image import convert_from_path

        rendered: dict[int, Any] = {}
        for page_number in pages:
            images = convert_from_path(
                pdf_path, dpi=self._dpi, first_page=page_number, last_page=page_number
            )
            if images:
                rendered[page_number] = images[0]
        return rendered

    def _extract_page(self, image: Any, page: PageProfile) -> ExtractedTable | None:
        words = self._recognize(image, page)
        if len(words) < 4:
            logger.warning("ocr_too_few_words", page=page.page_number, words=len(words))
            return None

        heights = [float(w["bottom"]) - float(w["top"]) for w in words]
        text_height = median(heights) if heights else 10.0
        line_tolerance = text_height * LINE_TOLERANCE_FACTOR
        lines = group_words_into_lines(words, line_tolerance)
        boundaries = find_column_boundaries(
            words,
            min_gap_width=text_height * MIN_GAP_FACTOR,
            min_empty_fraction=self._min_empty_fraction,
            line_tolerance=line_tolerance,
        )
        if not boundaries:
            logger.warning("ocr_no_columns_found", page=page.page_number)
            return None

        column_count = len(boundaries) + 1
        rows: list[tuple[str, ...]] = []
        for line in lines:
            buckets: list[list[str]] = [[] for _ in range(column_count)]
            for word in line:
                centre = (float(word["x0"]) + float(word["x1"])) / 2
                buckets[column_index_for(centre, boundaries)].append(str(word["text"]))
            cells = tuple(normalize_cell(" ".join(bucket)) for bucket in buckets)
            if any(cells):
                rows.append(cells)

        merged = merge_header_fragments(merge_continuation_rows(rows))
        if not merged:
            return None
        return ExtractedTable(
            page_number=page.page_number, table_index=0, rows=tuple(merged), strategy=self.name
        )

    def _recognize(self, image: Any, page: PageProfile) -> list[dict[str, Any]]:
        """Recognize the page, then keep only words inside its data regions."""
        import pytesseract
        from PIL import ImageOps

        grayscale = ImageOps.grayscale(image)
        data = pytesseract.image_to_data(
            grayscale, config=f"--psm {self._psm}", output_type=pytesseract.Output.DICT
        )

        scale_x = image.size[0] / page.width if page.width else 1.0
        scale_y = image.size[1] / page.height if page.height else 1.0
        regions = [
            (
                r.x0 * scale_x,
                r.top * scale_y,
                r.x1 * scale_x,
                min(r.bottom, page.height) * scale_y,
            )
            for r in page.image_regions
            if r.is_data_region
        ]

        words: list[dict[str, Any]] = []
        for index in range(len(data["text"])):
            text = str(data["text"][index]).strip()
            confidence = float(data["conf"][index])
            if not text or confidence < self._min_confidence:
                continue
            left = float(data["left"][index])
            top = float(data["top"][index])
            width = float(data["width"][index])
            height = float(data["height"][index])
            centre_x, centre_y = left + width / 2, top + height / 2
            if regions and not any(
                x0 <= centre_x <= x1 and y0 <= centre_y <= y1 for x0, y0, x1, y1 in regions
            ):
                continue
            words.append(
                {
                    "text": text,
                    "x0": left,
                    "x1": left + width,
                    "top": top,
                    "bottom": top + height,
                    "conf": confidence,
                }
            )
        return words


def merge_continuation_rows(rows: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Fold a wrapped line back into the record it belongs to.

    Scanned tables often set one column -- usually the owner name -- on its own baseline,
    so recognition emits it as a separate line holding nothing else. Left alone that line
    becomes a record with no parcel number, and the real record loses its owner.

    A line is treated as a continuation when it leaves the first column empty and fills
    only columns the previous line left empty. Both conditions matter: the first test says
    it does not begin a new record, and the second says it cannot be overwriting one.
    """
    merged: list[list[str]] = []
    for row in rows:
        filled = {i for i, cell in enumerate(row) if cell.strip()}
        if merged and filled and _is_continuation(merged[-1], row, filled):
            for index in filled:
                merged[-1][index] = row[index]
            continue
        merged.append(list(row))
    return [tuple(row) for row in merged]


MAX_HEADER_LABEL_LENGTH = 25
MAX_HEADER_FRAGMENT_LINES = 4
HEADER_SEARCH_DEPTH = 6


def merge_header_fragments(rows: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Rejoin a header that recognition split across several baselines.

    A stacked header such as "PARCEL / NUMBER" comes back as two lines, and a three-deep
    one as three. Left apart, header detection picks whichever fragment scores best and the
    rest become phantom data rows.

    Only leading lines are considered, and only while every cell on them is short and holds
    no money or date. That keeps a spanning title out of the merge: a title is one long
    phrase, and length is what separates it from a column label.
    """
    if not rows:
        return rows

    # A spanning title may sit above the header, so the run of fragments is located rather
    # than assumed to start at row zero. The title itself is left alone: it is one long
    # phrase, and length is what distinguishes it from a stack of column labels.
    limit = min(len(rows), HEADER_SEARCH_DEPTH)
    start = next((i for i in range(limit) if _is_header_fragment(rows[i])), None)
    if start is None:
        return rows

    end = start
    while end < min(len(rows), start + MAX_HEADER_FRAGMENT_LINES) and _is_header_fragment(
        rows[end]
    ):
        end += 1
    if end - start < 2:
        return rows

    width = max(len(row) for row in rows[start:end])
    combined: list[str] = []
    for column in range(width):
        parts = [row[column].strip() for row in rows[start:end] if column < len(row)]
        combined.append(" ".join(part for part in parts if part))
    return [*rows[:start], tuple(combined), *rows[end:]]


def _is_header_fragment(row: tuple[str, ...]) -> bool:
    values = [cell.strip() for cell in row if cell.strip()]
    if not values:
        return False
    if any(len(value) > MAX_HEADER_LABEL_LENGTH for value in values):
        return False
    return not any(looks_like_money(value) or looks_like_date(value) for value in values)


def _is_continuation(previous: list[str], row: tuple[str, ...], filled: set[int]) -> bool:
    if 0 in filled:
        return False
    previous_filled = {i for i, cell in enumerate(previous) if cell.strip()}
    return bool(previous_filled) and filled.isdisjoint(previous_filled)


def mean_word_confidence(words: list[dict[str, Any]]) -> float:
    """Average Tesseract word confidence, scaled to 0..1."""
    if not words:
        return 0.0
    return sum(float(w["conf"]) for w in words) / len(words) / 100.0
