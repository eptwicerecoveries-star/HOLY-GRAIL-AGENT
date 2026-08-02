from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
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

    county: Mapped[County] = relationship(back_populates="profile_versions")
