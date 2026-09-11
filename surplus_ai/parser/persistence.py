from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models.document_column_mapping import DocumentColumnMapping
from surplus_ai.database.models.enums import (
    MappingMethodType,
    ReviewStatus,
    RoutingDecisionType,
    SurplusSourceType,
)
from surplus_ai.database.models.parse_review_item import ParseReviewItem
from surplus_ai.database.models.parsed_document import ParsedDocument
from surplus_ai.database.models.raw_surplus_row import RawSurplusRow
from surplus_ai.parser.interpretation.models import (
    InterpretedDocument,
    InterpretedRow,
    RoutingDecision,
)
from surplus_ai.parser.interpretation.typed_cell_cleanup import REVIEW_REASON_TYPED_CELL
from surplus_ai.parser.interpretation.type_inference import has_usable_case_identity
from surplus_ai.parser.models import ParsedDocumentResult
from surplus_ai.utils.hashing import stable_row_hash

logger = structlog.get_logger(__name__)


class PersistenceReport:
    """What one persisted document produced."""

    def __init__(
        self,
        document_id: uuid.UUID,
        rows: int,
        mappings: int,
        queued_for_review: int,
        already_present: bool = False,
    ) -> None:
        self.document_id = document_id
        self.rows = rows
        self.mappings = mappings
        self.queued_for_review = queued_for_review
        self.already_present = already_present


class DocumentPersister:
    """Writes a parsed and interpreted document to the database.

    Four things are recorded together because they only make sense together: the document
    and how it was read, every row exactly as published, how each column was interpreted,
    and the rows a person still needs to look at.

    Re-persisting the same file is a no-op rather than a duplicate. Counties republish the
    same list with a new filename often enough that content, not the path, has to decide
    identity -- which is why the file's hash is what gets checked.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def persist(
        self,
        parsed: ParsedDocumentResult,
        interpreted: InterpretedDocument | None = None,
        county_id: uuid.UUID | None = None,
        ingestion_job_id: uuid.UUID | None = None,
    ) -> PersistenceReport:
        """Store a document, its rows, its column mappings and its review queue."""
        existing = self._session.scalar(
            select(ParsedDocument).where(
                ParsedDocument.source_file_sha256 == parsed.profile.source_sha256
            )
        )
        if existing is not None:
            logger.info(
                "document_already_persisted",
                path=str(parsed.profile.source_path),
                sha256=parsed.profile.source_sha256[:12],
            )
            return PersistenceReport(existing.id, 0, 0, 0, already_present=True)

        document = self._create_document(parsed, county_id, ingestion_job_id)
        rows = self._store_rows(parsed, interpreted, document, county_id)
        mappings = self._store_mappings(interpreted, document)
        queued = self._store_review_items(interpreted, document, county_id, rows)

        logger.info(
            "document_persisted",
            document_id=str(document.id),
            rows=len(rows),
            mappings=mappings,
            queued_for_review=queued,
        )
        return PersistenceReport(document.id, len(rows), mappings, queued)

    def _create_document(
        self,
        parsed: ParsedDocumentResult,
        county_id: uuid.UUID | None,
        ingestion_job_id: uuid.UUID | None,
    ) -> ParsedDocument:
        document = ParsedDocument(
            county_id=county_id,
            ingestion_job_id=ingestion_job_id,
            source_file_path=str(parsed.profile.source_path),
            source_file_sha256=parsed.profile.source_sha256,
            page_count=parsed.profile.page_count,
            pdf_type=parsed.profile.pdf_type,
            ocr_required=parsed.profile.ocr_required,
            winning_strategy=parsed.winning_strategy,
            table_count=len(parsed.tables),
            row_count=parsed.total_rows,
            classification_confidence=parsed.profile.confidence,
            extraction_confidence=parsed.extraction_confidence,
            page_profiles=[p.model_dump(mode="json") for p in parsed.profile.pages],
            strategy_scores=dict(parsed.strategy_scores),
            warnings=list(parsed.warnings),
        )
        self._session.add(document)
        self._session.flush()
        return document

    def _store_rows(
        self,
        parsed: ParsedDocumentResult,
        interpreted: InterpretedDocument | None,
        document: ParsedDocument,
        county_id: uuid.UUID | None,
    ) -> dict[tuple[int, int, int], RawSurplusRow]:
        """Persist every extracted row verbatim, keyed by its position for later lookup."""
        stored: dict[tuple[int, int, int], RawSurplusRow] = {}
        for table in parsed.tables:
            for row in table.rows:
                record = RawSurplusRow(
                    ingestion_job_id=document.ingestion_job_id,
                    county_id=county_id,
                    parsed_document_id=document.id,
                    raw_data=dict(row.values),
                    original_headers=list(table.original_headers),
                    row_hash=stable_row_hash(row.values),
                    source_pdf_sha256=row.source_pdf_sha256,
                    page_number=row.page_number,
                    table_index=row.table_index,
                    row_index_on_page=row.row_index_on_page,
                    extraction_method=row.extraction_method,
                    extraction_strategy=row.extraction_strategy,
                    extraction_confidence=row.confidence,
                )
                self._session.add(record)
                stored[(row.table_index, row.page_number, row.row_index_on_page)] = record
        self._session.flush()
        return stored

    def _store_mappings(
        self, interpreted: InterpretedDocument | None, document: ParsedDocument
    ) -> int:
        """Record how each published column was resolved, and on what evidence."""
        if interpreted is None:
            return 0
        count = 0
        for table in interpreted.tables:
            for position, mapping in enumerate(table.mappings):
                self._session.add(
                    DocumentColumnMapping(
                        parsed_document_id=document.id,
                        table_index=table.table_index,
                        column_position=position,
                        original_header=mapping.original_header[:300],
                        canonical_field=(
                            mapping.canonical_field.value if mapping.canonical_field else None
                        ),
                        method=MappingMethodType(mapping.method.value),
                        confidence=mapping.confidence,
                        evidence=mapping.evidence,
                    )
                )
                count += 1
        self._session.flush()
        return count

    def _store_review_items(
        self,
        interpreted: InterpretedDocument | None,
        document: ParsedDocument,
        county_id: uuid.UUID | None,
        rows: dict[tuple[int, int, int], RawSurplusRow],
    ) -> int:
        """Queue every row that may not be worked unattended."""
        if interpreted is None:
            return 0
        count = 0
        for table in interpreted.tables:
            for row in table.rows:
                if row.routing is RoutingDecision.AUTO_ACCEPT:
                    continue
                key = (row.table_index, row.page_number, row.row_index_on_page)
                raw_row = rows.get(key)
                self._session.add(
                    ParseReviewItem(
                        parsed_document_id=document.id,
                        raw_surplus_row_id=raw_row.id if raw_row else None,
                        county_id=county_id,
                        table_index=row.table_index,
                        page_number=row.page_number,
                        row_index_on_page=row.row_index_on_page,
                        raw_values=dict(row.raw_values),
                        canonical_values={
                            field.value: str(value)
                            for field, value in row.canonical_values.items()
                            if value is not None
                        },
                        routing=RoutingDecisionType(row.routing.value),
                        reason=review_reason(row, table.surplus.reason),
                        confidence=row.confidence,
                        extraction_method=row.extraction_method,
                        surplus_amount=row.surplus_amount,
                        surplus_source=SurplusSourceType(row.surplus_source.value),
                        status=ReviewStatus.PENDING,
                    )
                )
                count += 1
        self._session.flush()
        return count


def review_reason(row: InterpretedRow, surplus_reason: str) -> str:
    """Say plainly why a row needs a person, so a reviewer is not left guessing."""
    reasons: list[str] = []
    if row.extraction_method.value == "ocr":
        reasons.append(
            "Read by OCR, which never auto-accepts: a misread digit in a money field is "
            "expensive and recognition confidence does not predict that failure."
        )
    if row.surplus_source.value == "ambiguous":
        reasons.append(f"Surplus column could not be determined. {surplus_reason}")
    if not has_usable_case_identity(row.canonical_values):
        reasons.append(
            "No usable case identity (case number, certificate number, unique id, "
            "or a parcel-shaped identifier)."
        )
    if REVIEW_REASON_TYPED_CELL in row.review_reasons:
        reasons.append(
            "A typed identifier cell mixed a valid prefix with trailing text that "
            "could not be safely separated."
        )
    if row.coercion_failures:
        reasons.append("Values that could not be typed: " + "; ".join(row.coercion_failures))
    if not reasons:
        reasons.append(f"Confidence {row.confidence:.2f} is below the auto-accept threshold.")
    return " ".join(reasons)


class ReviewQueue:
    """The reviewer's work list."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def pending(self, county_id: uuid.UUID | None = None, limit: int = 50) -> list[ParseReviewItem]:
        statement = select(ParseReviewItem).where(ParseReviewItem.status == ReviewStatus.PENDING)
        if county_id is not None:
            statement = statement.where(ParseReviewItem.county_id == county_id)
        statement = statement.order_by(ParseReviewItem.confidence.asc()).limit(limit)
        return list(self._session.scalars(statement).all())

    def pending_count(self, county_id: uuid.UUID | None = None) -> int:
        statement = select(ParseReviewItem).where(ParseReviewItem.status == ReviewStatus.PENDING)
        if county_id is not None:
            statement = statement.where(ParseReviewItem.county_id == county_id)
        return len(list(self._session.scalars(statement).all()))

    def resolve(
        self,
        item_id: uuid.UUID,
        resolved_by: str,
        notes: str = "",
        status: ReviewStatus = ReviewStatus.RESOLVED,
    ) -> bool:
        """Close a queued row. The underlying extracted row is never touched."""
        item = self._session.get(ParseReviewItem, item_id)
        if item is None:
            return False
        item.status = status
        item.resolved_by = resolved_by
        item.resolved_at = datetime.now(UTC)
        item.resolution_notes = notes
        self._session.flush()
        logger.info("review_item_resolved", item_id=str(item_id), status=status.value)
        return True
