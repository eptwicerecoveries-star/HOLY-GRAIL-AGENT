from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import ExtractionMethod, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.county import County
    from surplus_ai.database.models.ingestion_job import IngestionJob


class RawSurplusRow(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "raw_surplus_rows"

    ingestion_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    county_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("counties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parsed_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parsed_documents.id", ondelete="SET NULL"), index=True
    )
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    original_headers: Mapped[list[str] | None] = mapped_column(JSONB)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_pdf_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer)
    table_index: Mapped[int | None] = mapped_column(Integer)
    row_index_on_page: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        pg_enum(ExtractionMethod, "extraction_method"), nullable=False
    )
    extraction_strategy: Mapped[str | None] = mapped_column(String(100))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)

    ingestion_job: Mapped[IngestionJob] = relationship(back_populates="raw_rows")
    county: Mapped[County] = relationship()
