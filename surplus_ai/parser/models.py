from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from surplus_ai.database.models.enums import ExtractionMethod, PageType, PdfType


class ImageRegion(BaseModel):
    """A raster image on a page, with the text density measured inside its bounds.

    `is_data_region` is decided by the classifier rather than by an exact zero-character
    test: a real scanned table can still contain a stray glyph or two from the surrounding
    layout, and one such character must not mask an 800-row image.
    """

    model_config = ConfigDict(frozen=True)

    x0: float
    top: float
    x1: float
    bottom: float
    area_ratio: float = Field(ge=0.0, description="Image area as a fraction of page area.")
    chars_inside: int = Field(ge=0)
    text_density: float = Field(ge=0.0, description="Characters per 1000 square units.")
    is_data_region: bool


class PageProfile(BaseModel):
    """Per-page verdict on how the page's content is encoded."""

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    width: float
    height: float
    char_count: int = Field(ge=0)
    word_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    line_count: int = Field(ge=0)
    rect_count: int = Field(ge=0)
    image_coverage_ratio: float = Field(ge=0.0)
    chars_outside_images: int = Field(ge=0)
    image_regions: tuple[ImageRegion, ...] = ()
    page_type: PageType
    ocr_required: bool
    confidence: float = Field(ge=0.0, le=1.0)


class DocumentProfile(BaseModel):
    """Document-level aggregate over PageProfiles."""

    model_config = ConfigDict(frozen=True)

    source_path: Path
    source_sha256: str
    page_count: int = Field(ge=0)
    pages: tuple[PageProfile, ...]
    pdf_type: PdfType
    ocr_required: bool
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def ocr_required_pages(self) -> tuple[int, ...]:
        return tuple(p.page_number for p in self.pages if p.ocr_required)

    @property
    def extractable_pages(self) -> tuple[int, ...]:
        """Pages whose tabular data can be read from the text layer.

        Pages needing OCR are excluded even when they carry plenty of text, because that
        text is the surrounding layout -- headings, disclaimers, navigation -- and not the
        table. Emitting it as rows would present chrome as data. Their text is preserved
        as unparsed fragments instead, and a later phase reads the image itself.
        """
        return tuple(
            p.page_number
            for p in self.pages
            if not p.ocr_required and p.page_type is not PageType.EMPTY
        )


class ExtractedTable(BaseModel):
    """One table as a strategy found it, before header interpretation.

    `rows` holds every row exactly as extracted, including any header row; nothing is
    removed at this stage so that header detection remains a separate, auditable decision.
    """

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    table_index: int = Field(ge=0)
    rows: tuple[tuple[str, ...], ...]
    strategy: str

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return max((len(r) for r in self.rows), default=0)


class ExtractionCandidate(BaseModel):
    """The full output of one strategy across the document, with its quality score."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    tables: tuple[ExtractedTable, ...]
    quality_score: float = Field(ge=0.0, le=1.0, default=0.0)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables)


class HeaderDetection(BaseModel):
    """Where the header is and what it says, once wrapped labels are rejoined."""

    model_config = ConfigDict(frozen=True)

    header_row_index: int = Field(ge=0)
    headers: tuple[str, ...]
    original_headers: tuple[str, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    duplicates_disambiguated: tuple[str, ...] = ()


class RawRow(BaseModel):
    """One verbatim data row, keyed by the county's own column names.

    Values are never trimmed beyond whitespace normalization, never renamed and never
    type-coerced at this layer. Provenance is carried on every row.
    """

    model_config = ConfigDict(frozen=True)

    values: dict[str, str]
    source_pdf_path: Path
    source_pdf_sha256: str
    page_number: int = Field(ge=1)
    table_index: int = Field(ge=0)
    row_index_on_page: int = Field(ge=0)
    extraction_method: ExtractionMethod
    extraction_strategy: str
    confidence: float = Field(ge=0.0, le=1.0)


class RawTable(BaseModel):
    """A logical table: its published headers plus every row that belongs to it.

    A logical table may span pages; each row still records the page it came from.
    """

    model_config = ConfigDict(frozen=True)

    table_index: int = Field(ge=0)
    original_headers: tuple[str, ...]
    rows: tuple[RawRow, ...]
    page_numbers: tuple[int, ...]
    header_confidence: float = Field(ge=0.0, le=1.0)

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ParsedDocumentResult(BaseModel):
    """Everything Phase 2A knows about one PDF.

    `unparsed_fragments` exists so that content which does not fit any table — titles,
    footers, banner lines — is retained rather than discarded.
    """

    model_config = ConfigDict(frozen=True)

    profile: DocumentProfile
    tables: tuple[RawTable, ...]
    winning_strategy: str
    strategy_scores: dict[str, float] = Field(default_factory=dict)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    unparsed_fragments: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables)

    @property
    def all_headers(self) -> tuple[tuple[str, ...], ...]:
        return tuple(t.original_headers for t in self.tables)
