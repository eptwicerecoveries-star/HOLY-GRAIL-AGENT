from __future__ import annotations

import structlog

from surplus_ai.parser.headers import HeaderReconstructor
from surplus_ai.parser.models import ExtractedTable, HeaderDetection

logger = structlog.get_logger(__name__)


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
    """

    def __init__(self, reconstructor: HeaderReconstructor | None = None) -> None:
        self._reconstructor = reconstructor or HeaderReconstructor()

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
                if current is not None:
                    self._append_continuation(current, table, None)
                    continue
                logger.warning(
                    "table_skipped_no_header", page=table.page_number, index=table.table_index
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

        Matching column count is the deciding signal, deliberately without requiring the
        page to carry a header. A headerless continuation page begins with a data row, and
        header detection will happily nominate that row because data rows still score
        moderately well as labels. Treating that nomination as evidence of a new table
        would split one county's list in two and promote a real record into a header,
        losing it. Column count is the stable property across a continued table.

        The trade-off is that two genuinely different tables with identical column counts
        merge. That is the safer failure: the rows survive with their true page numbers and
        can be separated later, whereas a promoted header row is data destroyed.
        """
        return table.column_count == len(current.header.headers)

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
        target.add_page_rows(table.page_number, rows[start:], start)


def _headers_match(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Compare header rows ignoring case and surrounding whitespace only."""
    if len(left) != len(right):
        return False
    return [c.strip().casefold() for c in left] == [c.strip().casefold() for c in right]
