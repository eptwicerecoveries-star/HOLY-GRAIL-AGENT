from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.models import DocumentProfile, ExtractedTable


class AbstractExtractionStrategy(ABC):
    """One way of getting tables out of a PDF.

    Strategies never branch on county identity. The pipeline runs several of them and
    keeps whichever produced the structurally best result, so a county the system has
    never seen is handled by the same code path as a familiar one.
    """

    name: str = "abstract"
    extraction_method: ExtractionMethod = ExtractionMethod.TABLE

    @abstractmethod
    def supports(self, profile: DocumentProfile) -> bool:
        """Whether this strategy can run at all against the profiled document."""

    @abstractmethod
    def extract(self, pdf_path: Path, profile: DocumentProfile) -> list[ExtractedTable]:
        """Return every table found, with rows exactly as read."""


def normalize_cell(value: str | None) -> str:
    """Collapse internal whitespace without altering the visible content.

    Wrapped cells arrive with embedded newlines; those become single spaces so a header
    reads `SALE AMOUNT` rather than `SALE\\nAMOUNT`. Nothing else about the text changes:
    no case folding, no punctuation stripping, no type coercion.
    """
    if value is None:
        return ""
    return " ".join(value.split())
