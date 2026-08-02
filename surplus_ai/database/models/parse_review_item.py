from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import (
    ExtractionMethod,
    ReviewStatus,
    RoutingDecisionType,
    SurplusSourceType,
    pg_enum,
)
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.parsed_document import ParsedDocument


class ParseReviewItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A row a person needs to look at before it can be used.

    Routing decides that a row is not safe to work unattended; this table is where such a
    row waits. It carries both the county's own values and the interpreted ones, so a
    reviewer can see what was published next to what was made of it without reopening the
    PDF.

    Queuing is not quarantining data. The row exists in `raw_surplus_rows` either way; this
    is a work list, and clearing it never deletes anything.
    """

    __tablename__ = "parse_review_items"
    __table_args__ = (
        UniqueConstraint(
            "parsed_document_id",
            "table_index",
            "page_number",
            "row_index_on_page",
            name="uq_parse_review_items_row",
        ),
    )

    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parsed_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_surplus_row_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("raw_surplus_rows.id", ondelete="SET NULL"), index=True
    )
    county_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("counties.id", ondelete="SET NULL"), index=True
    )

    table_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    row_index_on_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    raw_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    canonical_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    routing: Mapped[RoutingDecisionType] = mapped_column(
        pg_enum(RoutingDecisionType, "routing_decision_type"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        pg_enum(ExtractionMethod, "extraction_method"), nullable=False
    )

    surplus_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    surplus_source: Mapped[SurplusSourceType] = mapped_column(
        pg_enum(SurplusSourceType, "surplus_source_type"),
        nullable=False,
        default=SurplusSourceType.ABSENT,
    )

    status: Mapped[ReviewStatus] = mapped_column(
        pg_enum(ReviewStatus, "review_status"),
        nullable=False,
        default=ReviewStatus.PENDING,
        index=True,
    )
    resolved_by: Mapped[str | None] = mapped_column(String(200))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    parsed_document: Mapped[ParsedDocument] = relationship()
