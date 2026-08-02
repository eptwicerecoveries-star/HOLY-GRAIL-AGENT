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
