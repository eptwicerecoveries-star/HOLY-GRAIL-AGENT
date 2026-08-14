from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import SurplusCaseStatus, SurplusSourceType, pg_enum
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
    from surplus_ai.database.models.county import County
    from surplus_ai.database.models.lead import Lead
    from surplus_ai.database.models.owner import Owner
    from surplus_ai.database.models.property import Property
    from surplus_ai.database.models.research_result import ResearchResult
    from surplus_ai.database.models.research_review_item import ResearchReviewItem


class SurplusCase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "surplus_cases"
    __table_args__ = (
        UniqueConstraint("county_id", "dedupe_hash", name="uq_surplus_cases_county_dedupe_hash"),
    )

    county_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("counties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_row_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("raw_surplus_rows.id", ondelete="SET NULL"), index=True
    )
    # Nullable because most counties publish no case number at all -- they publish a parcel
    # or account number, or nothing but a name and an amount. Synthesising an identifier to
    # satisfy a NOT NULL would put a number in a field a reader would reasonably take for
    # the county's own. Identity lives in dedupe_hash, which is built for the purpose.
    case_number: Mapped[str | None] = mapped_column(String(200), index=True)
    parcel_id: Mapped[str | None] = mapped_column(String(200), index=True)
    property_address_raw: Mapped[str | None] = mapped_column(Text)
    property_address_normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sale_date: Mapped[date | None] = mapped_column(Date, index=True)
    judgment_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    sale_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Nullable by design: many counties publish sale/bid/assessment figures but never state
    # a surplus. Inferring one arithmetically would manufacture a lead, so absence is
    # recorded as NULL and consumers must treat NULL as "not a qualified lead", never zero.
    # A published 0.00 is different again: it is a real figure meaning nothing is left.
    surplus_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    surplus_is_explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    surplus_source: Mapped[SurplusSourceType] = mapped_column(
        pg_enum(SurplusSourceType, "surplus_source_type"),
        nullable=False,
        default=SurplusSourceType.ABSENT,
        index=True,
    )
    surplus_source_column: Mapped[str | None] = mapped_column(String(300))

    # Every other published money figure keeps its own column. Collapsing them would lose
    # the distinction between a gross bid, an amount already refunded, and money still held.
    sale_amount_published: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    winning_bid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    opening_bid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    assessed_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    appraised_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    taxes_due: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fees_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    face_value_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    overbid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    purchase_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    refunded_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    remaining_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    distribution_deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[SurplusCaseStatus] = mapped_column(
        pg_enum(SurplusCaseStatus, "surplus_case_status"),
        nullable=False,
        default=SurplusCaseStatus.NEW,
        index=True,
    )
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    county: Mapped[County] = relationship(back_populates="surplus_cases")
    owners: Mapped[list[Owner]] = relationship(
        back_populates="surplus_case", cascade="all, delete-orphan"
    )
    properties: Mapped[list[Property]] = relationship(
        back_populates="surplus_case", cascade="all, delete-orphan"
    )
    compliance_evaluations: Mapped[list[ComplianceEvaluation]] = relationship(
        back_populates="surplus_case", cascade="all, delete-orphan"
    )
    research_results: Mapped[list[ResearchResult]] = relationship(
        back_populates="surplus_case", cascade="all, delete-orphan"
    )
    research_review_items: Mapped[list[ResearchReviewItem]] = relationship(
        back_populates="surplus_case", cascade="all, delete-orphan"
    )
    lead: Mapped[Lead | None] = relationship(
        back_populates="surplus_case", cascade="all, delete-orphan", uselist=False
    )
