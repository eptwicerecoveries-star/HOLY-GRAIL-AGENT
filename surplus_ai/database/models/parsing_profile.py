from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.county import County


class ParsingProfileVersion(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    __tablename__ = "parsing_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "county_id", "version_hash", name="uq_parsing_profile_versions_county_hash"
        ),
    )

    county_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("counties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Profiles are append-only. Seeing the same layout again increments times_observed and
    # appends the file hash; a different layout creates a new row and marks this one
    # superseded. Nothing here is ever edited in place, so a layout change stays visible
    # and any past parse remains reproducible.
    times_observed: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observed_file_hashes: Mapped[list[str] | None] = mapped_column(JSONB)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    county: Mapped[County] = relationship(back_populates="profile_versions")
