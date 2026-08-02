from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import UserRole, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.interaction import Interaction
    from surplus_ai.database.models.lead import Lead


class User(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), nullable=False, default=UserRole.AGENT
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    assigned_leads: Mapped[list[Lead]] = relationship(back_populates="assigned_user")
    interactions: Mapped[list[Interaction]] = relationship(back_populates="user")
