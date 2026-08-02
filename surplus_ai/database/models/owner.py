from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import ClassificationMethod, OwnerType, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.surplus_case import SurplusCase


class Owner(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "owners"

    surplus_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("surplus_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_type: Mapped[OwnerType] = mapped_column(
        pg_enum(OwnerType, "owner_type"), nullable=False, default=OwnerType.UNKNOWN, index=True
    )
    first_name: Mapped[str | None] = mapped_column(String(200))
    middle_name: Mapped[str | None] = mapped_column(String(200))
    last_name: Mapped[str | None] = mapped_column(String(200))
    suffix: Mapped[str | None] = mapped_column(String(20))
    entity_name: Mapped[str | None] = mapped_column(String(500))
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    classification_method: Mapped[ClassificationMethod | None] = mapped_column(
        pg_enum(ClassificationMethod, "classification_method")
    )

    surplus_case: Mapped[SurplusCase] = relationship(back_populates="owners")
