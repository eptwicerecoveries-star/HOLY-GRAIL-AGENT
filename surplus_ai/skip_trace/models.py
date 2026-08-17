"""Typed skip-trace / contact-candidate contracts. In-memory only — no new tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContactCandidateType(str, Enum):
    """V1 materializable candidate kinds (subset of ContactType)."""

    PHONE = "phone"
    EMAIL = "email"


class SkipTraceStatus(str, Enum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"
    SKIPPED = "skipped"


class ContactCandidate(BaseModel):
    """Unverified contact evidence. Not a Contact ORM row until Lead-gated materialization."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    candidate_type: ContactCandidateType
    raw_value: str
    surplus_case_id: uuid.UUID | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_reference: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    requires_human_review: bool = False

    @field_validator("fetched_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class SkipTraceLookupResult(BaseModel):
    """Offline provider envelope. Never invents success. No raw vendor payload field."""

    model_config = ConfigDict(frozen=True)

    status: SkipTraceStatus
    provider_id: str
    lead_id: uuid.UUID
    surplus_case_id: uuid.UUID
    candidates: tuple[ContactCandidate, ...] = ()
    requires_human_review: bool = False
    error_code: str | None = None
    error_detail: str | None = None
    notes: str | None = None
