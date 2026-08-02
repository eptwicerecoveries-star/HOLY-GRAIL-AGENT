from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from surplus_ai.database.base import Base
from surplus_ai.database.models.enums import MappingMethodType, pg_enum
from surplus_ai.database.models.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from surplus_ai.database.models.parsed_document import ParsedDocument


class DocumentColumnMapping(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """How one published column of one document was interpreted.

    Two purposes. It is the audit trail a reviewer needs to answer "why did this column
    become that field?" without re-running anything. And it accumulates the corrections
    people make, which is the raw material for improving the alias registry: an
    unresolved header that keeps appearing across counties is a missing alias.
    """

    __tablename__ = "document_column_mappings"
    __table_args__ = (
        UniqueConstraint(
            "parsed_document_id",
            "table_index",
            "column_position",
            name="uq_document_column_mappings_position",
        ),
    )

    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parsed_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    table_index: Mapped[int] = mapped_column(Integer, nullable=False)
    column_position: Mapped[int] = mapped_column(Integer, nullable=False)
    original_header: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    canonical_field: Mapped[str | None] = mapped_column(String(100), index=True)
    method: Mapped[MappingMethodType] = mapped_column(
        pg_enum(MappingMethodType, "mapping_method_type"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence: Mapped[str | None] = mapped_column(Text)

    parsed_document: Mapped[ParsedDocument] = relationship()
