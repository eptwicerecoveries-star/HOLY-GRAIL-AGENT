from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import (
    ResearchReviewReason,
    ResearchReviewResolution,
    ReviewStatus,
    pg_enum,
)
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.research_result import ResearchResult
    from surplus_ai.database.models.surplus_case import SurplusCase


class ResearchReviewItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A work-list row for human inspection of persisted research evidence.

    Queuing is not a legal determination. Closing an item never authorizes contact,
    creates a Lead/Contact, or alters compliance eligibility. The triggering
    ``research_result_id`` is frozen at insert and is never retargeted.
    """

    __tablename__ = "research_review_items"
    __table_args__ = (
        Index(
            "uq_research_review_open_issue",
            "surplus_case_id",
            "provider",
            "reason",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    surplus_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("surplus_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NO ACTION (omit ondelete): block deleting a referenced ResearchResult while this
    # row still exists, without making SurplusCase CASCADE deletes immediate-RESTRICT
    # brittle. surplus_case_id CASCADE removes review items in the same statement.
    research_result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_results.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    reason: Mapped[ResearchReviewReason] = mapped_column(
        pg_enum(ResearchReviewReason, "research_review_reason"), nullable=False, index=True
    )
    reason_detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReviewStatus] = mapped_column(
        pg_enum(ReviewStatus, "review_status"),
        nullable=False,
        default=ReviewStatus.PENDING,
        index=True,
    )
    resolution: Mapped[ResearchReviewResolution | None] = mapped_column(
        pg_enum(ResearchReviewResolution, "research_review_resolution")
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_notes: Mapped[str | None] = mapped_column(Text)

    surplus_case: Mapped[SurplusCase] = relationship(back_populates="research_review_items")
    research_result: Mapped[ResearchResult] = relationship(back_populates="review_items")
