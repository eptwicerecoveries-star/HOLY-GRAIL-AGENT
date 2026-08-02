from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import ExportStatus, ExportType, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.county import County


class ExportBatch(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "export_batches"

    export_type: Mapped[ExportType] = mapped_column(
        pg_enum(ExportType, "export_type"), nullable=False, index=True
    )
    county_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("counties.id", ondelete="SET NULL"), index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(1000))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ExportStatus] = mapped_column(
        pg_enum(ExportStatus, "export_status"), nullable=False, default=ExportStatus.PENDING
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    county: Mapped[County | None] = relationship()
