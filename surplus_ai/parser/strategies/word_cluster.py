from __future__ import annotations

from pathlib import Path
from typing import Any

import pdfplumber
import structlog

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.exceptions import StrategyExecutionError
from surplus_ai.parser.models import DocumentProfile, ExtractedTable
from surplus_ai.parser.strategies.base import AbstractExtractionStrategy, normalize_cell

logger = structlog.get_logger(__name__)

DEFAULT_LINE_TOLERANCE = 3.0
DEFAULT_MIN_GAP_WIDTH = 6.0
DEFAULT_MIN_LINES = 3


class WordClusterStrategy(AbstractExtractionStrategy):
    """Derive columns from the whitespace between word boxes.

    Words are grouped into visual lines by their vertical position, then the horizontal
    space is scanned for gutters -- x ranges no word ever occupies. Those gutters are the
    column boundaries.

    This is the only strategy that needs nothing but word positions, which is why it also
    becomes the shared backend for OCR in a later phase: Tesseract emits word boxes in the
    same shape, so the same clustering applies to a scanned page.
    """

    name = "word_cluster"
    extraction_method = ExtractionMethod.TEXT

    def __init__(
        self,
        line_tolerance: float = DEFAULT_LINE_TOLERANCE,
        min_gap_width: float = DEFAULT_MIN_GAP_WIDTH,
        min_lines: int = DEFAULT_MIN_LINES,
    ) -> None:
        self._line_tolerance = line_tolerance
        self._min_gap_width = min_gap_width
        self._min_lines = min_lines

    def supports(self, profile: DocumentProfile) -> bool:
        return bool(profile.extractable_pages)

    def extract(self, pdf_path: Path, profile: DocumentProfile) -> list[ExtractedTable]:
        readable = set(profile.extractable_pages)
        tables: list[ExtractedTable] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for index, page in enumerate(pdf.pages):
                    page_number = index + 1
                    if page_number not in readable:
                        continue
                    table = self._extract_page(page, page_number)
                    if table is not None:
                        tables.append(table)
        except Exception as exc:
            logger.warning("strategy_failed", strategy=self.name, error=str(exc))
            raise StrategyExecutionError(f"{self.name} failed on {pdf_path}: {exc}") from exc
        return tables

    def _extract_page(self, page: Any, page_number: int) -> ExtractedTable | None:
        words = page.extract_words()
        if not words:
            return None

        lines = group_words_into_lines(words, self._line_tolerance)
        if len(lines) < self._min_lines:
            return None

        boundaries = find_column_boundaries(words, self._min_gap_width)
        if len(boundaries) < 1:
            return None

        rows: list[tuple[str, ...]] = []
        column_count = len(boundaries) + 1
        for line in lines:
            cells = [""] * column_count
            buckets: list[list[str]] = [[] for _ in range(column_count)]
            for word in line:
                centre = (float(word["x0"]) + float(word["x1"])) / 2
                buckets[column_index_for(centre, boundaries)].append(word["text"])
            for i, bucket in enumerate(buckets):
                cells[i] = normalize_cell(" ".join(bucket))
            if any(cells):
                rows.append(tuple(cells))

        if not rows:
            return None
        return ExtractedTable(
            page_number=page_number, table_index=0, rows=tuple(rows), strategy=self.name
        )


def group_words_into_lines(
    words: list[dict[str, Any]], tolerance: float
) -> list[list[dict[str, Any]]]:
    """Group words into visual lines by vertical proximity, each line sorted left to right."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    lines: list[list[dict[str, Any]]] = [[ordered[0]]]
    current_top = float(ordered[0]["top"])
    for word in ordered[1:]:
        top = float(word["top"])
        if abs(top - current_top) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
            current_top = top
    for line in lines:
        line.sort(key=lambda w: float(w["x0"]))
    return lines


def find_column_boundaries(words: list[dict[str, Any]], min_gap_width: float) -> list[float]:
    """Find x positions of the gutters that no word crosses.

    Occupancy is accumulated over one-unit buckets across the full width of the content;
    any unoccupied run at least `min_gap_width` wide, and not at the outer margins, is a
    column boundary taken at the midpoint of the gap.
    """
    if not words:
        return []
    x_min = min(float(w["x0"]) for w in words)
    x_max = max(float(w["x1"]) for w in words)
    if x_max <= x_min:
        return []

    span = int(x_max - x_min) + 1
    occupied = bytearray(span)
    for word in words:
        start = max(int(float(word["x0"]) - x_min), 0)
        end = min(int(float(word["x1"]) - x_min) + 1, span)
        for i in range(start, end):
            occupied[i] = 1

    boundaries: list[float] = []
    run_start: int | None = None
    for i in range(span):
        if not occupied[i]:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None:
            _append_gap(boundaries, run_start, i, x_min, min_gap_width)
            run_start = None
    # A trailing run touches the right margin and is not a column boundary.
    return boundaries


def _append_gap(
    boundaries: list[float], run_start: int, run_end: int, x_min: float, min_gap_width: float
) -> None:
    if run_start == 0:
        return  # leading margin, not a gutter between columns
    if (run_end - run_start) >= min_gap_width:
        boundaries.append(x_min + (run_start + run_end) / 2)


def column_index_for(centre: float, boundaries: list[float]) -> int:
    """Index of the column a word centre falls into, given ascending boundary positions."""
    index = 0
    for boundary in boundaries:
        if centre > boundary:
            index += 1
        else:
            break
    return index
