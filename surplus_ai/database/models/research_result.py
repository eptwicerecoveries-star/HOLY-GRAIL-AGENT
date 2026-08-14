from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import ResearchStatus, pg_enum
from surplus_ai.database.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.research_review_item import ResearchReviewItem
    from surplus_ai.database.models.surplus_case import SurplusCase


class ResearchResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "research_results"

    surplus_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("surplus_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[ResearchStatus] = mapped_column(
        pg_enum(ResearchStatus, "research_status"), nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    surplus_case: Mapped[SurplusCase] = relationship(back_populates="research_results")
    review_items: Mapped[list[ResearchReviewItem]] = relationship(
        back_populates="research_result"
    )
