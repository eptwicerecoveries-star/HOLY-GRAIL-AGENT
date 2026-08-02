from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from surplus_ai.database.base import Base
from surplus_ai.database.models.mixins import UUIDPrimaryKeyMixin


class AirtableSyncRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "airtable_sync_records"
    __table_args__ = (
        UniqueConstraint("local_table", "local_id", name="uq_airtable_sync_local"),
        UniqueConstraint(
            "airtable_table_name", "airtable_record_id", name="uq_airtable_sync_remote"
        ),
    )

    local_table: Mapped[str] = mapped_column(String(100), nullable=False)
    local_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    airtable_table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    airtable_record_id: Mapped[str] = mapped_column(String(50), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_hash: Mapped[str | None] = mapped_column(String(64))
