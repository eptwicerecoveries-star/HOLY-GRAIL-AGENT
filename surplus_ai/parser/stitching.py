from __future__ import annotations

import structlog

from surplus_ai.parser.headers import DEFAULT_MIN_HEADER_SCORE, HeaderReconstructor
from surplus_ai.parser.models import ExtractedTable, HeaderDetection
from surplus_ai.parser.quality import CURRENCY_RE, DATE_RE, IDENTIFIER_RE, INTEGER_RE, row_header_score

logger = structlog.get_logger(__name__)

# A continuation row whose cells are mostly dates, money, or identifiers is data, even
# when header detection nominates it. Below this share the row may still be labels.
_DATA_LIKE_SHARE = 0.4


class StitchedTable:
    """A logical table assembled from one or more pages.

    Rows keep the page they came from, so stitching never rewrites provenance.
    """

    def __init__(self, table_index: int, header: HeaderDetection) -> None:
        self.table_index = table_index
        self.header = header
        self.rows: list[tuple[int, int, tuple[str, ...]]] = []
        self.page_numbers: list[int] = []
        self.repeated_header_pages: list[int] = []

    def add_page_rows(
        self, page_number: int, rows: list[tuple[str, ...]], start_index: int
    ) -> None:
        for offset, row in enumerate(rows):
            self.rows.append((page_number, start_index + offset, row))
        if page_number not in self.page_numbers:
            self.page_numbers.append(page_number)


class TableStitcher:
    """Joins per-page tables into logical tables.

    Counties differ in opposite directions here and both appear in the corpus. One prints
    the header once and lets later pages continue headerless, so those pages must inherit.
    Another repeats the header on every page, so those repeats must be recognized and kept
    out of the data. Getting the second case wrong inflates the row count by exactly the
    page count, which is why the corpus checks totals against each document's own declared
    parcel count.

    A third failure is more subtle: header detection will nominate the first data row of a
    continuation page, and if that page also has a different extracted column count it
    must not become the continuation parent. Later compatible pages would then inherit
    the fake header instead of the real one.
    """

    def __init__(self, reconstructor: HeaderReconstructor | None = None) -> None:
        self._reconstructor = reconstructor or HeaderReconstructor()
        self._min_header_score = getattr(
            self._reconstructor, "_min_header_score", DEFAULT_MIN_HEADER_SCORE
        )

    def stitch(self, tables: list[ExtractedTable]) -> list[StitchedTable]:
        """Group page-level tables into logical tables, resolving header repetition."""
        stitched: list[StitchedTable] = []
        current: StitchedTable | None = None

        for table in sorted(tables, key=lambda t: (t.page_number, t.table_index)):
            if not table.rows:
                continue

            detection = self._try_detect(table)

            if current is not None and self._is_continuation(current, table, detection):
                self._append_continuation(current, table, detection)
                continue

            if detection is None:
                if current is not None and self._column_count_compatible(current, table):
                    self._append_continuation(current, table, None)
                    continue
                logger.warning(
                    "table_skipped_no_header", page=table.page_number, index=table.table_index
                )
                continue

            if current is not None and not self._is_label_like(table, detection):
                # Data-like nomination: do not let it become the continuation parent.
                isolated = StitchedTable(len(stitched), detection)
                self._append_continuation(isolated, table, None)
                stitched.append(isolated)
                logger.debug(
                    "continuation_header_rejected",
                    page=table.page_number,
                    columns=table.column_count,
                    parent_columns=len(current.header.headers),
                )
                continue

            current = StitchedTable(len(stitched), detection)
            self._append_continuation(current, table, detection)
            stitched.append(current)

        return stitched

    def _try_detect(self, table: ExtractedTable) -> HeaderDetection | None:
        try:
            return self._reconstructor.detect(table)
        except Exception:
            return None

    def _is_continuation(
        self,
        current: StitchedTable,
        table: ExtractedTable,
        detection: HeaderDetection | None,
    ) -> bool:
        """True when this page continues the table already in progress.

        Matching column count is required. A headerless continuation begins with a data
        row, and header detection will often nominate that row because data still scores
        moderately well as labels. That nomination is not evidence of a new table.

        A later table with its own label-like header is a new table even at the same
        width. Uncertain geometry fails closed: mismatched column counts do not inherit.
        """
        if not self._column_count_compatible(current, table):
            return False
        if current.header.confidence < self._min_header_score:
            return False
        if detection is None:
            return True
        if _headers_match(detection.headers, current.header.headers):
            return True
        if self._is_label_like(table, detection):
            return False
        return True

    def _column_count_compatible(self, current: StitchedTable, table: ExtractedTable) -> bool:
        return table.column_count == len(current.header.headers)

    def _is_label_like(self, table: ExtractedTable, detection: HeaderDetection) -> bool:
        """True when the nominated row looks like column labels rather than a data row."""
        if detection.confidence < self._min_header_score:
            return False
        row = table.rows[detection.header_row_index]
        if row_is_data_like(row):
            return False
        return row_header_score(row) >= self._min_header_score

    def _append_continuation(
        self,
        target: StitchedTable,
        table: ExtractedTable,
        detection: HeaderDetection | None,
    ) -> None:
        """Add a page's rows, dropping a repeated header row if one is present."""
        rows = list(table.rows)
        start = 0
        establishing_page = not target.page_numbers
        if detection is not None and _headers_match(detection.headers, target.header.headers):
            start = detection.header_row_index + 1
            # The page that first carried the header is not a repeat of it.
            if not establishing_page and table.page_number not in target.repeated_header_pages:
                target.repeated_header_pages.append(table.page_number)
        elif (
            not establishing_page
            and detection is not None
            and not _headers_match(detection.headers, target.header.headers)
            and not self._is_label_like(table, detection)
        ):
            # Nominated "header" is the first data row. Keep it as data.
            start = 0
        target.add_page_rows(table.page_number, rows[start:], start)


def row_is_data_like(row: tuple[str, ...]) -> bool:
    """True when enough cells look like published values rather than labels."""
    values = [cell.strip() for cell in row if cell.strip()]
    if len(values) < 2:
        return False
    data_cells = sum(1 for value in values if _cell_looks_like_data(value))
    return (data_cells / len(values)) >= _DATA_LIKE_SHARE


def _cell_looks_like_data(cell: str) -> bool:
    text = cell.strip()
    if not text:
        return False
    if DATE_RE.search(text) or CURRENCY_RE.match(text):
        return True
    if INTEGER_RE.match(text) and len(text) >= 4:
        return True
    if IDENTIFIER_RE.match(text) and any(character.isdigit() for character in text):
        return True
    return False


def _headers_match(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Compare header rows ignoring case and surrounding whitespace only."""
    if len(left) != len(right):
        return False
    return [c.strip().casefold() for c in left] == [c.strip().casefold() for c in right]
