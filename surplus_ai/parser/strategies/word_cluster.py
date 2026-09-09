from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber
import structlog

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.exceptions import StrategyExecutionError
from surplus_ai.parser.headers import DEFAULT_MIN_HEADER_SCORE, DEFAULT_SEARCH_DEPTH
from surplus_ai.parser.models import DocumentProfile, ExtractedTable
from surplus_ai.parser.quality import row_header_score
from surplus_ai.parser.stitching import row_is_data_like
from surplus_ai.parser.strategies.base import AbstractExtractionStrategy, normalize_cell

logger = structlog.get_logger(__name__)

DEFAULT_LINE_TOLERANCE = 3.0
DEFAULT_MIN_GAP_WIDTH = 6.0
DEFAULT_MIN_LINES = 3

# Word centres this close to a prior gutter are ambiguous: they might belong to either
# neighbouring band. Pages whose words crowd the inherited cuts fail closed.
DEFAULT_BOUNDARY_MARGIN = 3.0

# Both interior placement and occupied-band coverage must clear this. A layout that
# only uses two of eight prior columns, or that sits on the gutters, is not the same table.
DEFAULT_MIN_GEOMETRY_ALIGNMENT = 0.85
DEFAULT_MIN_OCCUPIED_FRACTION = 0.75


@dataclass(frozen=True)
class ColumnGeometry:
    """Logical column cuts established by a page that carried a real header."""

    boundaries: tuple[float, ...]
    header_confidence: float

    @property
    def column_count(self) -> int:
        return len(self.boundaries) + 1


class WordClusterStrategy(AbstractExtractionStrategy):
    """Derive columns from the whitespace between word boxes.

    Words are grouped into visual lines by their vertical position, then the horizontal
    space is scanned for gutters -- x ranges no word ever occupies. Those gutters are the
    column boundaries.

    Continuation pages of the same table often omit the header and pack values more tightly
    than the header row did, so a gutter that existed on the first page disappears. When a
    prior page established a strong header layout and this page's word centres still sit
    in those bands, the prior cuts are reused. Weak spatial agreement fails closed: the
    page keeps its own gutters and is not force-fit.

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
        min_geometry_alignment: float = DEFAULT_MIN_GEOMETRY_ALIGNMENT,
        boundary_margin: float = DEFAULT_BOUNDARY_MARGIN,
    ) -> None:
        self._line_tolerance = line_tolerance
        self._min_gap_width = min_gap_width
        self._min_lines = min_lines
        self._min_geometry_alignment = min_geometry_alignment
        self._boundary_margin = boundary_margin

    def supports(self, profile: DocumentProfile) -> bool:
        return bool(profile.extractable_pages)

    def extract(self, pdf_path: Path, profile: DocumentProfile) -> list[ExtractedTable]:
        readable = set(profile.extractable_pages)
        pages: list[tuple[int, list[dict[str, Any]]]] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for index, page in enumerate(pdf.pages):
                    page_number = index + 1
                    if page_number not in readable:
                        continue
                    words = page.extract_words()
                    if words:
                        pages.append((page_number, words))
        except Exception as exc:
            logger.warning("strategy_failed", strategy=self.name, error=str(exc))
            raise StrategyExecutionError(f"{self.name} failed on {pdf_path}: {exc}") from exc
        return extract_tables_from_word_pages(
            pages,
            line_tolerance=self._line_tolerance,
            min_gap_width=self._min_gap_width,
            min_lines=self._min_lines,
            min_alignment=self._min_geometry_alignment,
            boundary_margin=self._boundary_margin,
        )


def extract_tables_from_word_pages(
    pages: list[tuple[int, list[dict[str, Any]]]],
    *,
    line_tolerance: float = DEFAULT_LINE_TOLERANCE,
    min_gap_width: float = DEFAULT_MIN_GAP_WIDTH,
    min_lines: int = DEFAULT_MIN_LINES,
    min_header_score: float = DEFAULT_MIN_HEADER_SCORE,
    min_alignment: float = DEFAULT_MIN_GEOMETRY_ALIGNMENT,
    boundary_margin: float = DEFAULT_BOUNDARY_MARGIN,
) -> list[ExtractedTable]:
    """Build per-page tables, reusing a prior header page's column cuts when they fit.

    `pages` is an ordered sequence from one document. Geometry never jumps to another
    document because this function only sees the pages it is given.
    """
    tables: list[ExtractedTable] = []
    prior: ColumnGeometry | None = None
    for page_number, words in pages:
        table, established = _extract_page_words(
            words,
            page_number,
            prior=prior,
            line_tolerance=line_tolerance,
            min_gap_width=min_gap_width,
            min_lines=min_lines,
            min_header_score=min_header_score,
            min_alignment=min_alignment,
            boundary_margin=boundary_margin,
        )
        if table is None:
            continue
        tables.append(table)
        if established is not None:
            prior = established
    return tables


def _extract_page_words(
    words: list[dict[str, Any]],
    page_number: int,
    prior: ColumnGeometry | None,
    *,
    line_tolerance: float,
    min_gap_width: float,
    min_lines: int,
    min_header_score: float,
    min_alignment: float,
    boundary_margin: float,
) -> tuple[ExtractedTable | None, ColumnGeometry | None]:
    lines = group_words_into_lines(words, line_tolerance)
    if len(lines) < min_lines:
        return None, None

    native_bounds = find_column_boundaries(words, min_gap_width)
    native_rows = rows_from_words(words, native_bounds, line_tolerance) if native_bounds else []

    reused = False
    boundaries = native_bounds
    if prior is not None and prior_geometry_is_reusable(
        words,
        native_rows,
        prior,
        min_header_score=min_header_score,
        min_alignment=min_alignment,
        boundary_margin=boundary_margin,
    ):
        boundaries = list(prior.boundaries)
        reused = True
        logger.debug(
            "column_geometry_reused",
            page=page_number,
            columns=prior.column_count,
            alignment=round(
                geometry_alignment_score(words, prior.boundaries, margin=boundary_margin),
                3,
            ),
        )

    if len(boundaries) < 1:
        return None, None

    rows = rows_from_words(words, boundaries, line_tolerance)
    if not rows:
        return None, None

    table = ExtractedTable(
        page_number=page_number, table_index=0, rows=tuple(rows), strategy="word_cluster"
    )
    established = None if reused else geometry_from_native_header(
        boundaries, rows, min_header_score
    )
    return table, established


def prior_geometry_is_reusable(
    words: list[dict[str, Any]],
    native_rows: list[tuple[str, ...]],
    prior: ColumnGeometry,
    *,
    min_header_score: float,
    min_alignment: float,
    boundary_margin: float,
) -> bool:
    """True when this page is a headerless continuation that still sits in the prior bands."""
    if prior.header_confidence < min_header_score or not prior.boundaries:
        return False
    if _page_has_label_like_header(native_rows, min_header_score):
        return False
    score = geometry_alignment_score(words, list(prior.boundaries), margin=boundary_margin)
    return score >= min_alignment


def geometry_from_native_header(
    boundaries: list[float],
    rows: list[tuple[str, ...]],
    min_header_score: float,
) -> ColumnGeometry | None:
    """Keep cuts only when this page itself published a label-like header row."""
    if len(boundaries) < 1:
        return None
    row = _first_plausible_row(rows)
    if row is None or row_is_data_like(row):
        return None
    score = row_header_score(row)
    if score < min_header_score:
        return None
    return ColumnGeometry(boundaries=tuple(boundaries), header_confidence=score)


def geometry_alignment_score(
    words: list[dict[str, Any]],
    boundaries: list[float] | tuple[float, ...],
    margin: float = DEFAULT_BOUNDARY_MARGIN,
) -> float:
    """How cleanly word centres sit inside the given column bands, 0..1.

    The score is the lesser of (a) the share of words whose centre is at least `margin`
    away from every gutter and (b) the share of prior columns that received at least one
    word. Either signal failing keeps the result below a conservative reuse threshold.
    """
    bounds = list(boundaries)
    if not words or not bounds:
        return 0.0
    column_count = len(bounds) + 1
    occupied: set[int] = set()
    interior = 0
    for word in words:
        centre = (float(word["x0"]) + float(word["x1"])) / 2
        occupied.add(column_index_for(centre, bounds))
        nearest = min(abs(centre - bound) for bound in bounds)
        if nearest >= margin:
            interior += 1
    interior_share = interior / len(words)
    occupancy = len(occupied) / column_count
    if occupancy < DEFAULT_MIN_OCCUPIED_FRACTION:
        return 0.0
    return min(interior_share, occupancy)


def rows_from_words(
    words: list[dict[str, Any]],
    boundaries: list[float] | tuple[float, ...],
    line_tolerance: float = DEFAULT_LINE_TOLERANCE,
) -> list[tuple[str, ...]]:
    """Assign each line's words to column bands by horizontal centre."""
    bounds = list(boundaries)
    if not bounds:
        return []
    column_count = len(bounds) + 1
    rows: list[tuple[str, ...]] = []
    for line in group_words_into_lines(words, line_tolerance):
        buckets: list[list[str]] = [[] for _ in range(column_count)]
        for word in line:
            centre = (float(word["x0"]) + float(word["x1"])) / 2
            buckets[column_index_for(centre, bounds)].append(str(word["text"]))
        cells = tuple(normalize_cell(" ".join(bucket)) for bucket in buckets)
        if any(cells):
            rows.append(cells)
    return rows


def _page_has_label_like_header(rows: list[tuple[str, ...]], min_header_score: float) -> bool:
    row = _first_plausible_row(rows)
    if row is None or row_is_data_like(row):
        return False
    return row_header_score(row) >= min_header_score


def _first_plausible_row(rows: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    """Skip title-like lines that fill too few cells to be a table header."""
    if not rows:
        return None
    width = max(len(row) for row in rows)
    min_filled = max(2, width // 2)
    for row in rows[:DEFAULT_SEARCH_DEPTH]:
        if len([cell for cell in row if cell.strip()]) < min_filled:
            continue
        return row
    return None


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


def find_column_boundaries(
    words: list[dict[str, Any]],
    min_gap_width: float,
    min_empty_fraction: float = 1.0,
    line_tolerance: float = DEFAULT_LINE_TOLERANCE,
) -> list[float]:
    """Find x positions of the gutters between columns.

    Occupancy is counted per line rather than across all words at once, so a gutter can be
    recognised even when a few lines spill across it. `min_empty_fraction` is the share of
    lines that must leave a position clear: at 1.0 the gutter must be perfectly empty,
    which is what a text layer gives, and lowering it tolerates the ragged word boxes that
    recognition produces.

    That tolerance is not cosmetic. On the corpus's scanned county every position between
    the first and last column is covered by some line, because owner names run long and
    recognised boxes do not align as cleanly as typeset ones. Requiring perfect emptiness
    finds one gutter where there are four.
    """
    if not words:
        return []
    x_min = min(float(w["x0"]) for w in words)
    x_max = max(float(w["x1"]) for w in words)
    if x_max <= x_min:
        return []

    span = int(x_max - x_min) + 1
    lines = group_words_into_lines(words, line_tolerance)
    coverage = [0] * span
    for line in lines:
        marked = bytearray(span)
        for word in line:
            start = max(int(float(word["x0"]) - x_min), 0)
            end = min(int(float(word["x1"]) - x_min) + 1, span)
            for i in range(start, end):
                marked[i] = 1
        for i in range(span):
            if marked[i]:
                coverage[i] += 1

    allowed = len(lines) * (1.0 - min_empty_fraction)

    boundaries: list[float] = []
    run_start: int | None = None
    for i in range(span):
        if coverage[i] <= allowed:
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
