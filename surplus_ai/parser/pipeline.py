from __future__ import annotations

from pathlib import Path

import pdfplumber
import structlog

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.document_classifier import DocumentClassifier
from surplus_ai.parser.exceptions import (
    NoTableFoundError,
    OCRRequiredError,
    StrategyExecutionError,
)
from surplus_ai.parser.models import (
    DocumentProfile,
    ExtractionCandidate,
    ParsedDocumentResult,
    RawRow,
    RawTable,
)
from surplus_ai.parser.quality import score_tables
from surplus_ai.parser.row_continuation import fold_stitched_rows
from surplus_ai.parser.stitching import StitchedTable, TableStitcher
from surplus_ai.parser.strategies.base import AbstractExtractionStrategy
from surplus_ai.parser.strategies.ocr_table import OcrTableStrategy
from surplus_ai.parser.strategies.pdfplumber_strategies import (
    PdfplumberLinesStrategy,
    PdfplumberTextStrategy,
)
from surplus_ai.parser.strategies.word_cluster import WordClusterStrategy

logger = structlog.get_logger(__name__)

DEFAULT_MIN_ACCEPTABLE_SCORE = 0.30

# Strategies are compared on a sample of pages rather than the whole document. Which
# extractor reads a layout best is a property of the layout, and a county prints the same
# layout on every page, so a few pages settle it. Only the winner then runs over the full
# document, which keeps a 55-page file from being extracted three times over.
DEFAULT_SELECTION_SAMPLE_PAGES = 3


def default_strategies() -> list[AbstractExtractionStrategy]:
    """The cascade tried against every document, in order of typical cost.

    OCR comes last and claims only the pages the classifier flagged as image-only, so a
    document with a usable text layer never pays for recognition.
    """
    return [
        PdfplumberLinesStrategy(),
        PdfplumberTextStrategy(),
        WordClusterStrategy(),
        OcrTableStrategy(),
    ]


class ParsingPipeline:
    """Turns a county PDF into verbatim rows keyed by that county's own column names.

    No stage branches on which county produced the file. Strategies compete on structural
    quality, so an unfamiliar layout follows exactly the same path as a known one.
    """

    def __init__(
        self,
        classifier: DocumentClassifier | None = None,
        strategies: list[AbstractExtractionStrategy] | None = None,
        stitcher: TableStitcher | None = None,
        min_acceptable_score: float = DEFAULT_MIN_ACCEPTABLE_SCORE,
        selection_sample_pages: int = DEFAULT_SELECTION_SAMPLE_PAGES,
    ) -> None:
        self._classifier = classifier or DocumentClassifier()
        self._strategies = strategies if strategies is not None else default_strategies()
        self._stitcher = stitcher or TableStitcher()
        self._min_score = min_acceptable_score
        self._selection_sample_pages = selection_sample_pages

    def parse(self, pdf_path: Path) -> ParsedDocumentResult:
        """Profile, extract, stitch and emit verbatim rows for one PDF."""
        profile = self._classifier.classify(pdf_path)
        warnings: list[str] = []

        skipped = profile.ocr_required_pages
        if skipped:
            warnings.append(
                f"{len(skipped)} page(s) hold their data in an image and need OCR: "
                f"{list(skipped)}. Their text layer is retained as unparsed fragments."
            )

        if not profile.extractable_pages and not OcrTableStrategy.is_available():
            raise OCRRequiredError(
                f"Every data-bearing page of {pdf_path} stores its table as an image "
                f"(pages {list(skipped)}), and the OCR toolchain (tesseract, poppler) is "
                "not installed. Install it to read this document."
            )

        sample_profile = self._sample_profile(profile)
        candidates = self._run_cascade(pdf_path, sample_profile, warnings)
        if not candidates:
            raise NoTableFoundError(f"No extraction strategy produced a table for {pdf_path}")

        best = max(candidates, key=lambda c: c.quality_score)
        if best.quality_score < self._min_score:
            raise NoTableFoundError(
                f"Best strategy {best.strategy} scored {best.quality_score:.2f}, "
                f"below the minimum {self._min_score:.2f}, for {pdf_path}"
            )

        winner = self._strategy_named(best.strategy)
        if sample_profile.page_count < profile.page_count:
            full_tables = winner.extract(pdf_path, profile)
            score, breakdown = score_tables(full_tables)
            best = ExtractionCandidate(
                strategy=winner.name,
                tables=tuple(full_tables),
                quality_score=score,
                score_breakdown=breakdown,
            )

        method = winner.extraction_method
        tables = self._build_raw_tables(profile, best, method)
        if not tables:
            raise NoTableFoundError(f"No header could be established for any table in {pdf_path}")

        result = ParsedDocumentResult(
            profile=profile,
            tables=tuple(tables),
            winning_strategy=best.strategy,
            strategy_scores={c.strategy: c.quality_score for c in candidates},
            extraction_confidence=best.quality_score,
            unparsed_fragments=tuple(self._collect_fragments(pdf_path, profile, tables)),
            warnings=tuple(warnings),
        )
        logger.info(
            "document_parsed",
            path=str(pdf_path),
            strategy=best.strategy,
            score=round(best.quality_score, 3),
            tables=len(tables),
            rows=result.total_rows,
            warnings=len(warnings),
        )
        return result

    def _run_cascade(
        self, pdf_path: Path, profile: DocumentProfile, warnings: list[str]
    ) -> list[ExtractionCandidate]:
        candidates: list[ExtractionCandidate] = []
        for strategy in self._strategies:
            if not strategy.supports(profile):
                continue
            try:
                tables = strategy.extract(pdf_path, profile)
            except StrategyExecutionError as exc:
                warnings.append(f"strategy {strategy.name} failed: {exc}")
                continue
            if not tables:
                continue
            score, breakdown = score_tables(tables)
            candidates.append(
                ExtractionCandidate(
                    strategy=strategy.name,
                    tables=tuple(tables),
                    quality_score=score,
                    score_breakdown=breakdown,
                )
            )
            logger.debug(
                "strategy_scored",
                strategy=strategy.name,
                score=round(score, 3),
                tables=len(tables),
            )
        return candidates

    def _sample_profile(self, profile: DocumentProfile) -> DocumentProfile:
        """A profile trimmed to the first few readable pages, for strategy comparison."""
        extractable = profile.extractable_pages
        if len(extractable) <= self._selection_sample_pages:
            return profile
        keep = set(extractable[: self._selection_sample_pages])
        pages = tuple(p for p in profile.pages if p.page_number in keep)
        return profile.model_copy(update={"pages": pages, "page_count": len(pages)})

    def _strategy_named(self, strategy_name: str) -> AbstractExtractionStrategy:
        for strategy in self._strategies:
            if strategy.name == strategy_name:
                return strategy
        return self._strategies[0]

    def _build_raw_tables(
        self,
        profile: DocumentProfile,
        candidate: ExtractionCandidate,
        method: ExtractionMethod,
    ) -> list[RawTable]:
        stitched = self._stitcher.stitch(list(candidate.tables))
        tables: list[RawTable] = []
        for logical in stitched:
            rows = self._to_raw_rows(profile, logical, candidate, method)
            if not rows:
                continue
            tables.append(
                RawTable(
                    table_index=logical.table_index,
                    original_headers=logical.header.headers,
                    rows=tuple(rows),
                    page_numbers=tuple(logical.page_numbers),
                    header_confidence=logical.header.confidence,
                )
            )
        return tables

    def _to_raw_rows(
        self,
        profile: DocumentProfile,
        logical: StitchedTable,
        candidate: ExtractionCandidate,
        method: ExtractionMethod,
    ) -> list[RawRow]:
        headers = logical.header.headers
        folded = fold_stitched_rows(headers, logical.rows)
        rows: list[RawRow] = []
        for item in folded:
            values = pair_cells_with_headers(headers, item.cells)
            rows.append(
                RawRow(
                    values=values,
                    source_pdf_path=profile.source_path,
                    source_pdf_sha256=profile.source_sha256,
                    page_number=item.page_number,
                    table_index=logical.table_index,
                    row_index_on_page=item.row_index_on_page,
                    extraction_method=method,
                    extraction_strategy=candidate.strategy,
                    confidence=candidate.quality_score,
                    continuation_count=item.continuation_count,
                    continuation_sources=item.continuation_sources,
                )
            )
        return rows

    def _collect_fragments(
        self, pdf_path: Path, profile: DocumentProfile, tables: list[RawTable]
    ) -> list[str]:
        """Retain every line of text that did not end up in a table row.

        Two kinds of content would otherwise vanish. Titles and sale dates sit above the
        header and are not data rows, and banner lines such as a document's own declared
        parcel count sit outside the table entirely -- yet that count is exactly what
        proves the extraction was complete. Pages held back for OCR contribute their whole
        text layer here too, so a document we cannot fully read still gives up everything
        it does contain.
        """
        covered = self._covered_tokens_by_page(tables)
        ocr_pages = set(profile.ocr_required_pages)
        fragments: list[str] = []

        with pdfplumber.open(pdf_path) as pdf:
            for index, page in enumerate(pdf.pages):
                page_number = index + 1
                text = page.extract_text() or ""
                if not text.strip():
                    continue
                if page_number in ocr_pages:
                    fragments.append(f"[page {page_number}] {text}")
                    continue
                page_tokens = covered.get(page_number, set())
                for line in text.splitlines():
                    tokens = {t for t in line.split() if t}
                    if tokens and not tokens.issubset(page_tokens):
                        fragments.append(f"[page {page_number}] {line.strip()}")
        return fragments

    @staticmethod
    def _covered_tokens_by_page(tables: list[RawTable]) -> dict[int, set[str]]:
        """Tokens already captured as row content, indexed by page."""
        covered: dict[int, set[str]] = {}
        for table in tables:
            for row in table.rows:
                tokens = {token for value in row.values.values() for token in value.split()}
                covered.setdefault(row.page_number, set()).update(tokens)
                for page_number, _index in row.continuation_sources:
                    covered.setdefault(page_number, set()).update(tokens)
            for page_number in table.page_numbers:
                covered.setdefault(page_number, set()).update(
                    token for header in table.original_headers for token in header.split()
                )
        return covered


def pair_cells_with_headers(headers: tuple[str, ...], cells: tuple[str, ...]) -> dict[str, str]:
    """Map a row's cells onto the header labels without dropping either side.

    Ragged rows are normal in published PDFs. Extra cells beyond the header are kept under
    positional overflow keys rather than being thrown away, and missing trailing cells
    become empty strings so every row carries the same key set.
    """
    values: dict[str, str] = {}
    for index, header in enumerate(headers):
        values[header] = cells[index] if index < len(cells) else ""
    for index in range(len(headers), len(cells)):
        values[f"__overflow_{index + 1}"] = cells[index]
    return values
