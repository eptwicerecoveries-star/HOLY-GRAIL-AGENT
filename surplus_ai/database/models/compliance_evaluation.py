from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.surplus_case import SurplusCase


class ComplianceEvaluation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "compliance_evaluations"

    surplus_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("surplus_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    fee_cap_pct: Mapped[float | None] = mapped_column(Float)
    earliest_contact_date: Mapped[date | None] = mapped_column(Date)
    disclosures_required: Mapped[list[str] | None] = mapped_column(JSONB)
    evaluation_notes: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    surplus_case: Mapped[SurplusCase] = relationship(back_populates="compliance_evaluations")
