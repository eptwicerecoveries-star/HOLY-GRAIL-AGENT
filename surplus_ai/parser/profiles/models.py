from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from surplus_ai.database.models.enums import PdfType
from surplus_ai.parser.interpretation.models import SurplusSource


class TableStructure(BaseModel):
    """The shape of one logical table as it was found."""

    model_config = ConfigDict(frozen=True)

    table_index: int = Field(ge=0)
    column_count: int = Field(ge=0)
    original_columns: tuple[str, ...]
    header_row_index: int | None = None
    spans_pages: int = Field(ge=1, default=1)
    repeated_header_pages: int = Field(ge=0, default=0)


class OwnerTypeObservation(BaseModel):
    """What owner names in this county tend to look like.

    Recorded as observations only. Deciding whether an owner is an individual, a company
    or an estate is a later phase with its own rules, and guessing here would put an
    unreviewed classification into a stored profile.
    """

    model_config = ConfigDict(frozen=True)

    sampled: int = Field(ge=0, default=0)
    entity_like: int = Field(ge=0, default=0)
    person_like: int = Field(ge=0, default=0)

    @property
    def entity_share(self) -> float:
        return self.entity_like / self.sampled if self.sampled else 0.0


class CountyProfile(BaseModel):
    """What has been learned about how one county publishes its lists.

    A profile is a record of observation, not a decision. It never overrides a county's
    configuration file and never changes what was extracted; it exists so the next
    document from the same county starts from what the last one taught us.
    """

    model_config = ConfigDict(frozen=True)

    county_slug: str
    county_name: str = ""
    state: str = ""

    pdf_type: PdfType
    ocr_required: bool
    required_parsing_strategy: str
    table_structures: tuple[TableStructure, ...] = ()
    original_column_names: tuple[str, ...] = ()
    surplus_explicitly_listed: bool = False
    surplus_source: SurplusSource = SurplusSource.ABSENT
    surplus_source_column: str | None = None
    owner_types: OwnerTypeObservation = OwnerTypeObservation()

    typical_row_count: int = Field(ge=0, default=0)
    mean_extraction_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    unresolved_columns: tuple[str, ...] = ()

    def semantic_payload(self) -> dict[str, object]:
        """The parts of the profile that define its identity.

        Row counts, confidences and file hashes are deliberately excluded: a county that
        publishes the same layout with a different number of records has not changed its
        layout, and treating that as a new profile would fill the history with noise.
        """
        return {
            "county_slug": self.county_slug,
            "state": self.state,
            "pdf_type": self.pdf_type.value,
            "ocr_required": self.ocr_required,
            "required_parsing_strategy": self.required_parsing_strategy,
            "original_column_names": list(self.original_column_names),
            "surplus_explicitly_listed": self.surplus_explicitly_listed,
            "surplus_source": self.surplus_source.value,
            "surplus_source_column": self.surplus_source_column,
            "table_structures": [
                {
                    "table_index": t.table_index,
                    "column_count": t.column_count,
                    "original_columns": list(t.original_columns),
                }
                for t in self.table_structures
            ],
        }

    def version_hash(self) -> str:
        """A stable fingerprint of the layout this profile describes."""
        encoded = json.dumps(self.semantic_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StoredProfileVersion(BaseModel):
    """A profile as persisted, with the provenance that makes it auditable."""

    model_config = ConfigDict(frozen=True)

    county_slug: str
    version_hash: str
    profile: CountyProfile
    times_observed: int = Field(ge=1, default=1)
    observed_file_hashes: tuple[str, ...] = ()
    is_approved: bool = False
    superseded: bool = False
    created_at: datetime | None = None
