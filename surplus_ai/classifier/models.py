from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from surplus_ai.database.models.enums import OwnerType


class ClassificationMethodName(str, Enum):
    """How a classification was reached."""

    RULE = "rule"
    ML = "ml"
    MANUAL = "manual"


class PersonName(BaseModel):
    """A person's name split into parts, where the shape allowed it.

    Counties write names in at least two orders -- "STEFFEN, DEBORAH" and "GIBSON STEWART
    D" -- so the split is a best reading, not a guarantee. The raw name is always kept.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    first: str | None = None
    middle: str | None = None
    last: str | None = None
    suffix: str | None = None
    additional_parties: tuple[str, ...] = ()

    @property
    def is_split(self) -> bool:
        return self.first is not None or self.last is not None


class ClassificationResult(BaseModel):
    """What kind of party an owner name refers to, and how sure we are."""

    model_config = ConfigDict(frozen=True)

    raw_name: str
    owner_type: OwnerType
    confidence: float = Field(ge=0.0, le=1.0)
    method: ClassificationMethodName = ClassificationMethodName.RULE
    evidence: str = ""
    matched_markers: tuple[str, ...] = ()
    person_name: PersonName | None = None
    is_pursuable: bool = False

    @property
    def is_individual(self) -> bool:
        return self.owner_type is OwnerType.INDIVIDUAL
