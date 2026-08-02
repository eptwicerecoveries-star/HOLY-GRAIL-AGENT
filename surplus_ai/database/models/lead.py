from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import LeadStatus, pg_enum
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.call_sheet import CallSheetItem
    from surplus_ai.database.models.contact import Contact
    from surplus_ai.database.models.deal import Deal
    from surplus_ai.database.models.interaction import Interaction
    from surplus_ai.database.models.lead_score import LeadScore
    from surplus_ai.database.models.owner import Owner
    from surplus_ai.database.models.surplus_case import SurplusCase
    from surplus_ai.database.models.user import User


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "leads"

    surplus_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("surplus_cases.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("owners.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[LeadStatus] = mapped_column(
        pg_enum(LeadStatus, "lead_status"), nullable=False, default=LeadStatus.NEW, index=True
    )
    score: Mapped[float | None] = mapped_column(Float, index=True)
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    airtable_record_id: Mapped[str | None] = mapped_column(String(50), index=True)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    surplus_case: Mapped[SurplusCase] = relationship(back_populates="lead")
    owner: Mapped[Owner | None] = relationship()
    assigned_user: Mapped[User | None] = relationship(back_populates="assigned_leads")
    contacts: Mapped[list[Contact]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    scores: Mapped[list[LeadScore]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    interactions: Mapped[list[Interaction]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    deal: Mapped[Deal | None] = relationship(
        back_populates="lead", cascade="all, delete-orphan", uselist=False
    )
    call_sheet_items: Mapped[list[CallSheetItem]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
