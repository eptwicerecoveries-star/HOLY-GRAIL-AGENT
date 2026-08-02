from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import CountySourceType, PublishingFrequency, pg_enum
from surplus_ai.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.ingestion_job import IngestionJob
    from surplus_ai.database.models.parsing_profile import ParsingProfileVersion
    from surplus_ai.database.models.surplus_case import SurplusCase


class County(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "counties"
    __table_args__ = (UniqueConstraint("state", "slug", name="uq_counties_state_slug"),)

    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    fips_code: Mapped[str | None] = mapped_column(String(5))
    source_type: Mapped[CountySourceType] = mapped_column(
        pg_enum(CountySourceType, "county_source_type"), nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(1000))
    parsing_profile_key: Mapped[str] = mapped_column(String(100), nullable=False)
    compliance_state_ref: Mapped[str] = mapped_column(String(2), nullable=False)
    publishing_frequency: Mapped[PublishingFrequency] = mapped_column(
        pg_enum(PublishingFrequency, "publishing_frequency"),
        nullable=False,
        default=PublishingFrequency.IRREGULAR,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    profile_versions: Mapped[list[ParsingProfileVersion]] = relationship(
        back_populates="county", cascade="all, delete-orphan"
    )
    ingestion_jobs: Mapped[list[IngestionJob]] = relationship(
        back_populates="county", cascade="all, delete-orphan"
    )
    surplus_cases: Mapped[list[SurplusCase]] = relationship(
        back_populates="county", cascade="all, delete-orphan"
    )
