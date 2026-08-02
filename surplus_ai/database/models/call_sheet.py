from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.lead import Lead
    from surplus_ai.database.models.user import User


class CallSheet(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "call_sheets"
    __table_args__ = (
        UniqueConstraint("run_date", "assigned_user_id", name="uq_call_sheets_date_user"),
    )

    run_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lead_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    assigned_user: Mapped[User | None] = relationship()
    items: Mapped[list[CallSheetItem]] = relationship(
        back_populates="call_sheet", cascade="all, delete-orphan"
    )


class CallSheetItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "call_sheet_items"
    __table_args__ = (
        UniqueConstraint("call_sheet_id", "lead_id", name="uq_call_sheet_items_sheet_lead"),
    )

    call_sheet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("call_sheets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    called: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    call_sheet: Mapped[CallSheet] = relationship(back_populates="items")
    lead: Mapped[Lead] = relationship(back_populates="call_sheet_items")
