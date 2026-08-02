from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import IngestionJobStatus, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.county import County
    from surplus_ai.database.models.raw_surplus_row import RawSurplusRow


class IngestionJob(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "ingestion_jobs"

    county_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("counties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[IngestionJobStatus] = mapped_column(
        pg_enum(IngestionJobStatus, "ingestion_job_status"),
        nullable=False,
        default=IngestionJobStatus.PENDING,
    )
    rows_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(100), nullable=False, default="cli")

    county: Mapped[County] = relationship(back_populates="ingestion_jobs")
    raw_rows: Mapped[list[RawSurplusRow]] = relationship(
        back_populates="ingestion_job", cascade="all, delete-orphan"
    )
