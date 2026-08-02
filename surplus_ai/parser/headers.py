from __future__ import annotations

import structlog

from surplus_ai.parser.exceptions import HeaderDetectionError
from surplus_ai.parser.models import ExtractedTable, HeaderDetection
from surplus_ai.parser.quality import row_header_score

logger = structlog.get_logger(__name__)

DEFAULT_SEARCH_DEPTH = 6
DEFAULT_MIN_HEADER_SCORE = 0.55


class HeaderReconstructor:
    """Finds the header row and produces stable, unique column names.

    Two things make this harder than reading row zero. Headers are often preceded by a
    title and a date line, so the header can be several rows down. And headers wrap across
    physical lines, arriving as cells like `SALE\\nAMOUNT`; the wrap has already been
    rejoined into `SALE AMOUNT` by the extraction layer, which keeps the label exactly as
    published rather than guessing at word order from the linearized text.
    """

    def __init__(
        self,
        search_depth: int = DEFAULT_SEARCH_DEPTH,
        min_header_score: float = DEFAULT_MIN_HEADER_SCORE,
    ) -> None:
        self._search_depth = search_depth
        self._min_header_score = min_header_score

    def detect(self, table: ExtractedTable) -> HeaderDetection:
        """Locate the header row and return its labels, disambiguating duplicates."""
        if not table.rows:
            raise HeaderDetectionError("Cannot detect a header in an empty table")

        width = table.column_count
        best_index, best_score = -1, 0.0
        for index, row in enumerate(table.rows[: self._search_depth]):
            # A header spans the table; title lines occupy one cell and must not win.
            if len([c for c in row if c.strip()]) < max(2, width // 2):
                continue
            score = row_header_score(row)
            if score > best_score:
                best_index, best_score = index, score

        if best_index < 0 or best_score < self._min_header_score:
            raise HeaderDetectionError(
                f"No plausible header row found in the first {self._search_depth} rows "
                f"(best score {best_score:.2f} at row {best_index})"
            )

        original = tuple(table.rows[best_index])
        headers, disambiguated = disambiguate(original)
        logger.debug(
            "header_detected",
            page=table.page_number,
            row_index=best_index,
            score=round(best_score, 3),
            headers=list(headers),
        )
        return HeaderDetection(
            header_row_index=best_index,
            headers=headers,
            original_headers=original,
            confidence=min(best_score, 1.0),
            duplicates_disambiguated=disambiguated,
        )


def disambiguate(headers: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Make column names unique without discarding any column.

    Counties do repeat labels, and blank header cells are common. Both would collapse
    columns together if used as dictionary keys, silently losing data, so each repeat gets
    a positional suffix and each blank gets a positional placeholder.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    changed: list[str] = []

    for position, raw in enumerate(headers):
        label = raw.strip() or f"column_{position + 1}"
        if not raw.strip():
            changed.append(label)
        count = seen.get(label.casefold(), 0) + 1
        seen[label.casefold()] = count
        if count > 1:
            suffixed = f"{label}__{count}"
            changed.append(suffixed)
            result.append(suffixed)
        else:
            result.append(label)

    return tuple(result), tuple(changed)
