from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import ContactType, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.lead import Lead


class Contact(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint(
            "lead_id",
            "contact_type",
            "value",
            name="uq_contacts_lead_contact_type_value",
        ),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_type: Mapped[ContactType] = mapped_column(
        pg_enum(ContactType, "contact_type"), nullable=False, index=True
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[float | None] = mapped_column(Float)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    lead: Mapped[Lead] = relationship(back_populates="contacts")
