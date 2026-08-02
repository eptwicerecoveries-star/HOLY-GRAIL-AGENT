from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pdfplumber
import structlog

from surplus_ai.database.models.enums import PageType, PdfType
from surplus_ai.parser.exceptions import UnreadablePDFError
from surplus_ai.parser.models import DocumentProfile, ImageRegion, PageProfile

logger = structlog.get_logger(__name__)

# An image must cover at least this fraction of the page before it is considered a
# candidate data region. Smaller images are logos, seals and buttons.
DEFAULT_MIN_DATA_REGION_AREA_RATIO = 0.10

# A page needs at least this many characters outside its images before its text layer is
# considered usable for extraction.
DEFAULT_MIN_USABLE_CHARS = 25

# Characters per 1000 square units, below which a region is treated as carrying no text.
# Measured on the corpus: a genuine text page runs about 9.1, while St. Mary's scanned
# table region measures 0.002 -- a single stray glyph over 436k square units. Testing for
# exactly zero characters would let that one glyph hide an entire scanned table.
DEFAULT_MIN_TEXT_DENSITY = 0.5


class DocumentClassifier:
    """Decides, per page, whether the data can be read from the text layer or needs OCR.

    The decision is deliberately made per *region* rather than per document. A page can
    carry plenty of text — headings, disclaimers, navigation chrome — while the table
    itself is a raster image. Asking only "does this PDF have a text layer?" classifies
    such a page as searchable and silently yields zero rows.
    """

    def __init__(
        self,
        min_data_region_area_ratio: float = DEFAULT_MIN_DATA_REGION_AREA_RATIO,
        min_usable_chars: int = DEFAULT_MIN_USABLE_CHARS,
        min_text_density: float = DEFAULT_MIN_TEXT_DENSITY,
    ) -> None:
        self._min_area_ratio = min_data_region_area_ratio
        self._min_usable_chars = min_usable_chars
        self._min_text_density = min_text_density

    def classify(self, pdf_path: Path) -> DocumentProfile:
        """Profile every page of the PDF and aggregate to a document verdict."""
        if not pdf_path.is_file():
            raise UnreadablePDFError(f"PDF not found: {pdf_path}")

        sha256 = sha256_of_file(pdf_path)
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages = tuple(
                    self._profile_page(page, index + 1) for index, page in enumerate(pdf.pages)
                )
        except UnreadablePDFError:
            raise
        except Exception as exc:
            logger.error("pdf_open_failed", path=str(pdf_path), error=str(exc))
            raise UnreadablePDFError(f"Could not read {pdf_path}: {exc}") from exc

        pdf_type, ocr_required, confidence = self._aggregate(pages)
        profile = DocumentProfile(
            source_path=pdf_path,
            source_sha256=sha256,
            page_count=len(pages),
            pages=pages,
            pdf_type=pdf_type,
            ocr_required=ocr_required,
            confidence=confidence,
        )
        logger.info(
            "document_classified",
            path=str(pdf_path),
            pages=len(pages),
            pdf_type=pdf_type.value,
            ocr_required=ocr_required,
            ocr_pages=profile.ocr_required_pages,
            confidence=round(confidence, 3),
        )
        return profile

    def _profile_page(self, page: Any, page_number: int) -> PageProfile:
        width = float(page.width)
        height = float(page.height)
        page_area = width * height or 1.0

        chars = page.chars
        images = page.images
        regions: list[ImageRegion] = []
        chars_in_large_regions = 0

        for image in images:
            x0, top = float(image["x0"]), float(image["top"])
            x1, bottom = float(image["x1"]), float(image["bottom"])
            region_area = abs(x1 - x0) * abs(bottom - top)
            area_ratio = region_area / page_area
            if area_ratio < self._min_area_ratio:
                continue
            inside = _count_chars_inside(chars, x0, top, x1, bottom)
            chars_in_large_regions += inside
            density = inside / (region_area / 1000.0) if region_area > 0 else 0.0
            regions.append(
                ImageRegion(
                    x0=x0,
                    top=top,
                    x1=x1,
                    bottom=bottom,
                    area_ratio=area_ratio,
                    chars_inside=inside,
                    text_density=density,
                    is_data_region=density < self._min_text_density,
                )
            )

        coverage = min(sum(r.area_ratio for r in regions), 1.0)
        chars_outside = max(len(chars) - chars_in_large_regions, 0)
        empty_data_regions = [r for r in regions if r.is_data_region]

        page_type, ocr_required, confidence = self._decide(
            char_count=len(chars),
            chars_outside=chars_outside,
            region_count=len(regions),
            empty_region_count=len(empty_data_regions),
        )

        return PageProfile(
            page_number=page_number,
            width=width,
            height=height,
            char_count=len(chars),
            word_count=len(page.extract_words()) if chars else 0,
            image_count=len(images),
            line_count=len(page.lines),
            rect_count=len(page.rects),
            image_coverage_ratio=coverage,
            chars_outside_images=chars_outside,
            image_regions=tuple(regions),
            page_type=page_type,
            ocr_required=ocr_required,
            confidence=confidence,
        )

    def _decide(
        self,
        char_count: int,
        chars_outside: int,
        region_count: int,
        empty_region_count: int,
    ) -> tuple[PageType, bool, float]:
        """Classify one page from its text/image signals.

        The interesting case is the last one: a page with a healthy text layer *and* a
        large empty image region is a hybrid, and the table is inside the image.
        """
        if char_count == 0 and region_count == 0:
            return PageType.EMPTY, False, 0.9
        if char_count == 0:
            return PageType.IMAGE_ONLY, True, 0.95
        if empty_region_count > 0:
            if chars_outside < self._min_usable_chars:
                return PageType.IMAGE_ONLY, True, 0.9
            return PageType.HYBRID, True, 0.85
        if chars_outside < self._min_usable_chars:
            return PageType.EMPTY, False, 0.6
        return PageType.TEXT, False, 0.95

    def _aggregate(self, pages: tuple[PageProfile, ...]) -> tuple[PdfType, bool, float]:
        if not pages:
            return PdfType.EMPTY, False, 0.5

        types = {p.page_type for p in pages}
        ocr_required = any(p.ocr_required for p in pages)
        confidence = sum(p.confidence for p in pages) / len(pages)

        if types == {PageType.EMPTY}:
            return PdfType.EMPTY, False, confidence
        if types <= {PageType.TEXT, PageType.EMPTY}:
            return PdfType.SEARCHABLE, False, confidence
        if types <= {PageType.IMAGE_ONLY, PageType.EMPTY}:
            return PdfType.SCANNED, True, confidence
        return PdfType.HYBRID, ocr_required, confidence


def _count_chars_inside(
    chars: list[dict[str, Any]], x0: float, top: float, x1: float, bottom: float
) -> int:
    """Count characters whose centre falls within the given rectangle."""
    count = 0
    for char in chars:
        cx = (float(char["x0"]) + float(char["x1"])) / 2
        cy = (float(char["top"]) + float(char["bottom"])) / 2
        if x0 <= cx <= x1 and top <= cy <= bottom:
            count += 1
    return count


def sha256_of_file(path: Path) -> str:
    """Stream a file through SHA-256 so provenance survives renames and re-downloads."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
