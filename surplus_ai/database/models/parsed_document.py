from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import PdfType, pg_enum
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.county import County
    from surplus_ai.database.models.ingestion_job import IngestionJob


class ParsedDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per PDF processed: how it was read and how well.

    Kept separate from IngestionJob because a job may process several files, and because
    this record is the provenance a reviewer needs when auditing an extracted row.
    """

    __tablename__ = "parsed_documents"

    county_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("counties.id", ondelete="SET NULL"), index=True
    )
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), index=True
    )
    source_file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_type: Mapped[PdfType] = mapped_column(pg_enum(PdfType, "pdf_type"), nullable=False)
    ocr_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    winning_strategy: Mapped[str | None] = mapped_column(String(100))
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    page_profiles: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    strategy_scores: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)

    county: Mapped[County | None] = relationship()
    ingestion_job: Mapped[IngestionJob | None] = relationship()
