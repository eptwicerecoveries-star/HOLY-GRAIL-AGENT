from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.surplus_case import SurplusCase


class Property(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "properties"

    surplus_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("surplus_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parcel_id: Mapped[str | None] = mapped_column(String(200), index=True)
    assessed_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    legal_description: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(String(100))
    last_researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    surplus_case: Mapped[SurplusCase] = relationship(back_populates="properties")
