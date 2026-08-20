"""Pydantic response schemas for the local read-only API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class ReadyResponse(BaseModel):
    status: str = "ready"


class StatusResponse(BaseModel):
    application: str
    version: str
    environment: str
    database_reachable: bool
    alembic_current: str | None
    alembic_head: str | None
    migrations_up_to_date: bool
    local_only: bool = True
    authentication: str = "session_cookie"
    warning: str = (
        "LOCAL SESSION-AUTHENTICATED API MUST NOT BE INTERNET-FACING. "
        "Bind to 127.0.0.1 only."
    )


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128, repr=False)


class AuthUserResponse(BaseModel):
    """Safe authenticated-user projection. No password or session secrets."""

    id: UUID
    name: str
    email: str
    role: str
    is_active: bool


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    county_id: UUID
    county_state: str
    county_slug: str
    county_name: str
    parcel_id: str | None
    case_number: str | None
    surplus_amount: Decimal | None
    surplus_is_explicit: bool
    status: str
    sale_date: date | None
    has_lead: bool
    created_at: datetime
    updated_at: datetime


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    surplus_case_id: UUID
    status: str
    score: float | None
    assigned_user_id: UUID | None
    contact_count: int
    qualified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResearchReviewResponse(BaseModel):
    """Operational review metadata only — no raw research payloads."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    surplus_case_id: UUID
    research_result_id: UUID
    provider: str
    reason: str
    reason_detail: str | None
    status: str
    resolution: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContactResponse(BaseModel):
    """Contact metadata only — Contact.value is intentionally omitted."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    contact_type: str
    source: str | None
    confidence: float | None
    is_verified: bool
    created_at: datetime


class ErrorResponse(BaseModel):
    code: str
    message: str = Field(description="Safe client-facing message")
