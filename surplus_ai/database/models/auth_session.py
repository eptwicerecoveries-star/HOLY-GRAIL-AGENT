from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.user import User


class AuthSession(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Server-side session row. Stores SHA-256(token) only — never the raw token."""

    __tablename__ = "auth_sessions"
    __table_args__ = (UniqueConstraint("token_digest", name="uq_auth_sessions_token_digest"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship()
