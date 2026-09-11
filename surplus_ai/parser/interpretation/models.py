from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.interpretation.canonical import CanonicalField


class MappingMethod(str, Enum):
    """How a published column was resolved to a canonical field."""

    COUNTY_OVERRIDE = "county_override"
    EXACT_ALIAS = "exact_alias"
    FUZZY_ALIAS = "fuzzy_alias"
    VALUE_INFERENCE = "value_inference"
    SURPLUS_EXPLICIT = "surplus_explicit"
    UNRESOLVED = "unresolved"


class SurplusSource(str, Enum):
    """Where a case's surplus figure came from, or why there is none."""

    EXPLICIT = "explicit"
    """The county published a column naming surplus, and only one such column existed."""

    COUNTY_CONFIG = "county_config"
    """A county configuration file pinned which published column is the surplus."""

    DERIVED = "derived"
    """Computed from other columns under an explicitly configured, county-confirmed rule."""

    AMBIGUOUS = "ambiguous"
    """Several columns could be the surplus. Nothing is chosen and the amount stays null."""

    ABSENT = "absent"
    """The county published no surplus column. The amount is null, never inferred."""


class RoutingDecision(str, Enum):
    """What may be done with a row without a person looking at it first."""

    AUTO_ACCEPT = "auto_accept"
    REVIEW = "review"
    QUARANTINE = "quarantine"


class ColumnMapping(BaseModel):
    """One published column's resolution, with the evidence behind it."""

    model_config = ConfigDict(frozen=True)

    original_header: str
    canonical_field: CanonicalField | None
    method: MappingMethod
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.canonical_field is not None


class SurplusResolution(BaseModel):
    """The verdict on whether this county publishes claimable surplus, and where."""

    model_config = ConfigDict(frozen=True)

    source: SurplusSource
    source_column: str | None = None
    is_explicit: bool = False
    candidates: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    reason: str = ""

    @property
    def has_surplus_column(self) -> bool:
        return self.source_column is not None


class InterpretedRow(BaseModel):
    """A row expressed in the universal schema, with the verbatim original alongside.

    `raw_values` is the county's own wording and is never modified. `canonical_values`
    holds typed values for the columns that resolved. `unmapped` keeps every column that
    did not resolve, so an unrecognised label costs visibility, never data.
    """

    model_config = ConfigDict(frozen=True)

    raw_values: dict[str, str]
    canonical_values: dict[CanonicalField, Decimal | date | str | None] = Field(
        default_factory=dict
    )
    unmapped: dict[str, str] = Field(default_factory=dict)
    surplus_amount: Decimal | None = None
    surplus_is_explicit: bool = False
    surplus_source: SurplusSource = SurplusSource.ABSENT
    surplus_source_column: str | None = None
    source_pdf_path: Path
    source_pdf_sha256: str
    page_number: int = Field(ge=1)
    table_index: int = Field(ge=0)
    row_index_on_page: int = Field(ge=0)
    extraction_method: ExtractionMethod
    extraction_strategy: str
    confidence: float = Field(ge=0.0, le=1.0)
    routing: RoutingDecision
    coercion_failures: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()

    def value(self, field: CanonicalField) -> Decimal | date | str | None:
        return self.canonical_values.get(field)


class InterpretedTable(BaseModel):
    model_config = ConfigDict(frozen=True)

    table_index: int = Field(ge=0)
    original_headers: tuple[str, ...]
    mappings: tuple[ColumnMapping, ...]
    surplus: SurplusResolution
    rows: tuple[InterpretedRow, ...]

    @property
    def resolved_fields(self) -> tuple[CanonicalField, ...]:
        return tuple(m.canonical_field for m in self.mappings if m.canonical_field is not None)

    @property
    def unresolved_headers(self) -> tuple[str, ...]:
        return tuple(m.original_header for m in self.mappings if not m.is_resolved)


class InterpretedDocument(BaseModel):
    """The full interpretation of one PDF."""

    model_config = ConfigDict(frozen=True)

    source_path: Path
    source_sha256: str
    tables: tuple[InterpretedTable, ...]
    county_config_used: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def total_rows(self) -> int:
        return sum(len(t.rows) for t in self.tables)

    @property
    def rows_with_surplus(self) -> int:
        """Rows carrying a surplus figure at all, including a published zero."""
        return sum(1 for t in self.tables for r in t.rows if r.surplus_amount is not None)

    @property
    def rows_with_claimable_surplus(self) -> int:
        """Rows where money is actually still owed.

        A published zero is a real figure, not a missing one: a fully refunded overbid
        genuinely reads $0.00 and the county holds nothing. Those rows are stored and
        interpreted like any other, but they are not opportunities, so the two counts are
        reported separately rather than one standing in for the other.
        """
        return sum(
            1
            for t in self.tables
            for r in t.rows
            if r.surplus_amount is not None and r.surplus_amount > 0
        )

    def routing_counts(self) -> dict[RoutingDecision, int]:
        counts = dict.fromkeys(RoutingDecision, 0)
        for table in self.tables:
            for row in table.rows:
                counts[row.routing] += 1
        return counts
