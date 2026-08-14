from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy import Enum as SAEnum


class CountySourceType(str, Enum):
    BULK_DOWNLOAD = "bulk_download"
    SFTP = "sftp"
    EMAIL = "email"
    API = "api"
    MANUAL_UPLOAD = "manual_upload"


class PublishingFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    IRREGULAR = "irregular"


class IngestionJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionMethod(str, Enum):
    TABLE = "table"
    TEXT = "text"
    OCR = "ocr"
    LLM_ASSIST = "llm_assist"


class PdfType(str, Enum):
    """How a document's data regions are encoded, aggregated across its pages."""

    SEARCHABLE = "searchable"
    SCANNED = "scanned"
    HYBRID = "hybrid"
    EMPTY = "empty"


class PageType(str, Enum):
    """Per-page verdict. IMAGE_ONLY means the data region carries no text layer."""

    TEXT = "text"
    IMAGE_ONLY = "image_only"
    HYBRID = "hybrid"
    EMPTY = "empty"


class SurplusSourceType(str, Enum):
    """Where a case's surplus figure came from, or why it has none.

    Persisted so a null surplus is never ambiguous: "the county published none" and
    "several columns rivalled each other and we refused to guess" are different facts and
    lead to different follow-up.
    """

    EXPLICIT = "explicit"
    COUNTY_CONFIG = "county_config"
    DERIVED = "derived"
    AMBIGUOUS = "ambiguous"
    ABSENT = "absent"


class MappingMethodType(str, Enum):
    """How a published column was resolved to a canonical field."""

    COUNTY_OVERRIDE = "county_override"
    EXACT_ALIAS = "exact_alias"
    FUZZY_ALIAS = "fuzzy_alias"
    VALUE_INFERENCE = "value_inference"
    SURPLUS_EXPLICIT = "surplus_explicit"
    UNRESOLVED = "unresolved"


class RoutingDecisionType(str, Enum):
    """What may be done with a parsed row without a person looking at it first."""

    AUTO_ACCEPT = "auto_accept"
    REVIEW = "review"
    QUARANTINE = "quarantine"


class ReviewStatus(str, Enum):
    """Where a queued row stands in the reviewer workflow."""

    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class ResearchReviewReason(str, Enum):
    """Why a persisted research result was queued for a person.

    Workflow signal only. Never a legal claimant, heir, entitlement, or
    contactability conclusion.
    """

    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    COMPLEX_OWNER_CONTEXT = "complex_owner_context"
    MANUAL_RESEARCH_REQUIRED = "manual_research_required"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_FAILURE = "provider_failure"


class ResearchReviewResolution(str, Enum):
    """Human evidence/workflow conclusion. Never legal entitlement or outreach."""

    EVIDENCE_USABLE = "evidence_usable"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NEEDS_ADDITIONAL_RESEARCH = "needs_additional_research"
    CONFLICT_UNRESOLVED = "conflict_unresolved"
    NOT_RELEVANT = "not_relevant"


class SurplusCaseStatus(str, Enum):
    NEW = "new"
    NORMALIZED = "normalized"
    CLASSIFIED = "classified"
    COMPLIANCE_CHECKED = "compliance_checked"
    RESEARCHED = "researched"
    QUALIFIED = "qualified"
    REJECTED = "rejected"


class OwnerType(str, Enum):
    INDIVIDUAL = "individual"
    COMPANY = "company"
    TRUST = "trust"
    ESTATE = "estate"
    GOVERNMENT = "government"
    UNKNOWN = "unknown"


class ClassificationMethod(str, Enum):
    RULE = "rule"
    ML = "ml"
    MANUAL = "manual"


class LeadStatus(str, Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    INTERESTED = "interested"
    CONTRACT_SENT = "contract_sent"
    CONTRACT_SIGNED = "contract_signed"
    CLAIM_FILED = "claim_filed"
    PAID = "paid"
    DEAD = "dead"


class ContactType(str, Enum):
    PHONE = "phone"
    EMAIL = "email"
    MAILING_ADDRESS = "mailing_address"


class ResearchStatus(str, Enum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"


class InteractionType(str, Enum):
    CALL = "call"
    EMAIL = "email"
    SMS = "sms"
    MAIL = "mail"
    NOTE = "note"


class DealStage(str, Enum):
    CONTRACT_SENT = "contract_sent"
    CONTRACT_SIGNED = "contract_signed"
    CLAIM_FILED = "claim_filed"
    PAID = "paid"
    LOST = "lost"


class UserRole(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    AGENT = "agent"


class ExportType(str, Enum):
    CSV = "csv"
    AIRTABLE = "airtable"


class ExportStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


def pg_enum(enum_cls: type[Enum], name: str) -> SAEnum:
    """Build a named DB enum that persists member values rather than member names."""

    def _values(cls: type[Enum]) -> list[Any]:
        return [member.value for member in cls]

    return SAEnum(enum_cls, name=name, values_callable=_values, native_enum=True)
