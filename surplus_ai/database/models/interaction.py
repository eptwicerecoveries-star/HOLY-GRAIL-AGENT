from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import InteractionType, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.lead import Lead
    from surplus_ai.database.models.user import User


class Interaction(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "interactions"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    interaction_type: Mapped[InteractionType] = mapped_column(
        pg_enum(InteractionType, "interaction_type"), nullable=False, index=True
    )
    outcome: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    lead: Mapped[Lead] = relationship(back_populates="interactions")
    user: Mapped[User | None] = relationship(back_populates="interactions")
