"""Login throttle bucket rows (P4-B2-A). Digest-only keys; no raw IP/email."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from surplus_ai.database.base import Base


class LoginThrottleBucket(Base):
    """Aggregate throttle state keyed by scope + SHA-256 digest."""

    __tablename__ = "login_throttle_buckets"
    __table_args__ = (
        PrimaryKeyConstraint("scope", "key_digest", name="pk_login_throttle_buckets"),
        CheckConstraint("event_count >= 0", name="ck_login_throttle_buckets_event_count"),
        CheckConstraint(
            "scope IN ('ip', 'credential')",
            name="ck_login_throttle_buckets_scope",
        ),
    )

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
