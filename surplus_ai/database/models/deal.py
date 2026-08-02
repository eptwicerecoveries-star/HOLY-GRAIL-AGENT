from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import DealStage, pg_enum
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.lead import Lead


class Deal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "deals"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    stage: Mapped[DealStage] = mapped_column(
        pg_enum(DealStage, "deal_stage"),
        nullable=False,
        default=DealStage.CONTRACT_SENT,
        index=True,
    )
    contract_fee_pct: Mapped[float | None] = mapped_column(Float)
    claim_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lead: Mapped[Lead] = relationship(back_populates="deal")
