from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.lead import Lead


class LeadScore(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "lead_scores"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score_version: Mapped[str] = mapped_column(String(50), nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    factor_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lead: Mapped[Lead] = relationship(back_populates="scores")
