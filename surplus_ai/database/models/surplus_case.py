from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import SurplusCaseStatus, pg_enum
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
    from surplus_ai.database.models.county import County
    from surplus_ai.database.models.lead import Lead
    from surplus_ai.database.models.owner import Owner
    from surplus_ai.database.models.property import Property
    from surplus_ai.database.models.research_result import ResearchResult


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
    case_number: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    parcel_id: Mapped[str | None] = mapped_column(String(200), index=True)
    property_address_raw: Mapped[str | None] = mapped_column(Text)
    property_address_normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sale_date: Mapped[date | None] = mapped_column(Date, index=True)
    judgment_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    sale_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Nullable by design: many counties publish sale/bid/assessment figures but never state
    # a surplus. Inferring one arithmetically would manufacture a lead, so absence is
    # recorded as NULL and consumers must treat NULL as "not a qualified lead", never zero.
    surplus_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
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
    lead: Mapped[Lead | None] = relationship(
        back_populates="surplus_case", cascade="all, delete-orphan", uselist=False
    )
