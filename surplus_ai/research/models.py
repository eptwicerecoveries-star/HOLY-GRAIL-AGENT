"""Versioned DTOs for Phase 6 research (schema_version on every envelope)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PAYLOAD_SCHEMA_VERSION: Literal[1] = 1


class ResearchKind(str, Enum):
    PROPERTY = "property"
    SKIP_TRACE = "skip_trace"
    ENRICHMENT = "enrichment"
    ENTITY = "entity"


class ResearchMethod(str, Enum):
    OFFICIAL_API = "official_api"
    OPEN_DATA = "open_data"
    SKIP_TRACE = "skip_trace"
    LOCAL_NORMALIZE = "local_normalize"
    MANUAL = "manual"
    NONE = "none"


class ProviderOutcomeStatus(str, Enum):
    """Provider-level outcome. Mapped onto ResearchStatus for persistence."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class EvidenceAtom(BaseModel):
    """One researched fact with full provenance. Never implies legal entitlement."""

    model_config = ConfigDict(frozen=True)

    field: str
    original_value: str | None
    normalized_value: str | None
    source: str
    source_url: str | None = None
    retrieved_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    method: ResearchMethod
    requires_human_verification: bool
    owner_type_context: str | None = None

    @field_validator("retrieved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class ProviderOutcome(BaseModel):
    """Universal provider return envelope. Failures never become success."""

    model_config = ConfigDict(frozen=True)

    status: ProviderOutcomeStatus
    found: bool = False
    evidence: tuple[EvidenceAtom, ...] = ()
    source_url: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    requires_human_review: bool = False
    cacheable: bool = True
    retryable: bool = False
    notes: str | None = None
    raw_response: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _enforce_fail_closed(self) -> ProviderOutcome:
        if self.found and self.status is not ProviderOutcomeStatus.SUCCESS:
            raise ValueError("found=True is only allowed when status is success")
        if self.status is ProviderOutcomeStatus.SUCCESS and not self.found:
            raise ValueError("status=success requires found=True")
        return self


class PropertyLookupQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: str
    county_slug: str
    parcel_id: str | None = None
    owner_raw_name: str | None = None
    property_address_raw: str | None = None
    sale_date: str | None = None
    owner_type: str | None = None
    surplus_case_id: uuid.UUID | None = None


class ResearchCandidate(BaseModel):
    """A case eligible for property research. Not a Lead and not a Contact."""

    model_config = ConfigDict(frozen=True)

    surplus_case_id: uuid.UUID
    county_id: uuid.UUID
    state: str
    county_slug: str
    parcel_id: str | None
    property_address_raw: str | None
    owner_raw_name: str | None
    owner_type: str | None
    sale_date: str | None
    selection_reason: str


class RequestPayload(BaseModel):
    """Versioned JSON stored on research_results.request_payload."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = PAYLOAD_SCHEMA_VERSION
    research_kind: ResearchKind = ResearchKind.PROPERTY
    cache_key: str
    county_state: str
    county_slug: str
    parcel_id: str | None = None
    owner_raw_name: str | None = None
    property_address_raw: str | None = None
    query: dict[str, Any] = Field(default_factory=dict)
    provider: str
    # Never include API keys or secrets here.


class ResponsePayload(BaseModel):
    """Versioned JSON stored on research_results.response_payload."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = PAYLOAD_SCHEMA_VERSION
    found: bool
    source_url: str | None = None
    requires_human_review: bool
    error_code: str | None = None
    error_detail: str | None = None
    evidence: tuple[EvidenceAtom, ...] = ()
    notes: str | None = None
    provider_status: ProviderOutcomeStatus
    # Explicit JSON true/false in 6B. Missing/None on 6A rows is a cache miss.
    cacheable: bool | None = None
    # Sanitized provider echo only — no credentials.
    raw_response: dict[str, Any] | None = None


class ResearchRunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    cases_attempted: int = 0
    results_written: int = 0
    success: int = 0
    not_found: int = 0
    error: int = 0

    def summary_lines(self) -> list[str]:
        return [
            f"cases attempted:  {self.cases_attempted}",
            f"results written:  {self.results_written}",
            f"  success:        {self.success}",
            f"  not_found:      {self.not_found}",
            f"  error:          {self.error}",
        ]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
