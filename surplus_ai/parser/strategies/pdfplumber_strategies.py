from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pdfplumber
import structlog

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.exceptions import StrategyExecutionError
from surplus_ai.parser.models import DocumentProfile, ExtractedTable
from surplus_ai.parser.strategies.base import AbstractExtractionStrategy, normalize_cell

logger = structlog.get_logger(__name__)


class _PdfplumberStrategy(AbstractExtractionStrategy):
    """Shared driver for the pdfplumber-backed strategies.

    Subclasses differ only in their table settings, which is what makes them genuinely
    independent opinions about where the column boundaries are.
    """

    table_settings: ClassVar[dict[str, Any]] = {}
    extraction_method = ExtractionMethod.TABLE

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
                    tables.extend(self._extract_page(page, page_number))
        except Exception as exc:
            logger.warning("strategy_failed", strategy=self.name, error=str(exc))
            raise StrategyExecutionError(f"{self.name} failed on {pdf_path}: {exc}") from exc
        return tables

    def _extract_page(self, page: Any, page_number: int) -> list[ExtractedTable]:
        results: list[ExtractedTable] = []
        raw_tables = page.extract_tables(self.table_settings) or []
        for table_index, raw in enumerate(raw_tables):
            rows = tuple(
                tuple(normalize_cell(cell) for cell in row) for row in raw if _has_content(row)
            )
            if not rows:
                continue
            results.append(
                ExtractedTable(
                    page_number=page_number,
                    table_index=table_index,
                    rows=rows,
                    strategy=self.name,
                )
            )
        return results


class PdfplumberLinesStrategy(_PdfplumberStrategy):
    """Column boundaries from ruling lines and rectangle edges.

    Effective even when a PDF reports zero explicit lines, because pdfplumber derives
    edges from rectangles too -- which is how every searchable file in the corpus is
    actually laid out.
    """

    name = "pdfplumber_lines"
    table_settings: ClassVar[dict[str, Any]] = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
    }


class PdfplumberTextStrategy(_PdfplumberStrategy):
    """Column boundaries inferred from whitespace gaps between words."""

    name = "pdfplumber_text"
    table_settings: ClassVar[dict[str, Any]] = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
    }


def _has_content(row: list[str | None]) -> bool:
    return any(cell and cell.strip() for cell in row)
