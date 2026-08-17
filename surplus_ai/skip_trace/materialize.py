"""Lead-gated Contact materialization. Acquisition is separate from this service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.enums import ContactType
from surplus_ai.database.models.lead import Lead
from surplus_ai.skip_trace.models import ContactCandidate, ContactCandidateType
from surplus_ai.skip_trace.normalize import normalize_email, normalize_phone

logger = structlog.get_logger(__name__)

_TYPE_MAP = {
    ContactCandidateType.PHONE: ContactType.PHONE,
    ContactCandidateType.EMAIL: ContactType.EMAIL,
}


class MaterializeBlockReason(str, Enum):
    LEAD_NOT_FOUND = "lead_not_found"
    LEAD_CASE_MISMATCH = "lead_case_mismatch"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    INVALID_CONTACT = "invalid_contact"
    UNSUPPORTED_TYPE = "unsupported_type"


@dataclass(frozen=True)
class ContactMaterializeResult:
    """Field names / IDs only — never phone/email values."""

    materialized: bool
    blocked: bool
    block_reason: MaterializeBlockReason | None
    lead_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    provider_id: str | None
    contact_type: str | None
    created: bool
    already_present: bool


def materialize_contact_candidate(
    session: Session,
    *,
    lead_id: uuid.UUID,
    candidate: ContactCandidate,
) -> ContactMaterializeResult:
    """Create or reuse a Contact for an existing Lead. Caller owns the transaction.

    Does not create Lead. Does not call providers. Does not mutate Owner/Compliance.
    Does not send communications. Compliance remains authoritative for outreach.
    Database unique (lead_id, contact_type, value) is the concurrency authority.
    """
    lead = session.get(Lead, lead_id)
    if lead is None:
        return _blocked(
            MaterializeBlockReason.LEAD_NOT_FOUND,
            lead_id=lead_id,
            provider_id=candidate.provider_id,
            contact_type=candidate.candidate_type.value,
        )

    if (
        candidate.surplus_case_id is not None
        and candidate.surplus_case_id != lead.surplus_case_id
    ):
        return _blocked(
            MaterializeBlockReason.LEAD_CASE_MISMATCH,
            lead_id=lead_id,
            provider_id=candidate.provider_id,
            contact_type=candidate.candidate_type.value,
        )

    if candidate.requires_human_review:
        return _blocked(
            MaterializeBlockReason.HUMAN_REVIEW_REQUIRED,
            lead_id=lead_id,
            provider_id=candidate.provider_id,
            contact_type=candidate.candidate_type.value,
        )

    contact_type = _TYPE_MAP.get(candidate.candidate_type)
    if contact_type is None:
        return _blocked(
            MaterializeBlockReason.UNSUPPORTED_TYPE,
            lead_id=lead_id,
            provider_id=candidate.provider_id,
            contact_type=candidate.candidate_type.value,
        )

    normalized = _normalize(candidate.candidate_type, candidate.raw_value)
    if normalized is None:
        return _blocked(
            MaterializeBlockReason.INVALID_CONTACT,
            lead_id=lead_id,
            provider_id=candidate.provider_id,
            contact_type=candidate.candidate_type.value,
        )

    existing = _find_exact(session, lead.id, contact_type, normalized)
    if existing is not None:
        return _already_present(existing, provider_id=candidate.provider_id)

    row = Contact(
        lead_id=lead.id,
        contact_type=contact_type,
        value=normalized,
        source=candidate.provider_id[:100] if candidate.provider_id else None,
        confidence=candidate.confidence,
        is_verified=False,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        recovered = _find_exact(session, lead.id, contact_type, normalized)
        if recovered is None:
            raise
        logger.info(
            "contact_materialize_race_recovered",
            lead_id=str(lead.id),
            contact_id=str(recovered.id),
            provider_id=candidate.provider_id,
            contact_type=contact_type.value,
            created=False,
            already_present=True,
        )
        return _already_present(recovered, provider_id=candidate.provider_id)

    logger.info(
        "contact_materialize_created",
        lead_id=str(lead.id),
        contact_id=str(row.id),
        provider_id=candidate.provider_id,
        contact_type=contact_type.value,
        created=True,
        already_present=False,
    )
    return ContactMaterializeResult(
        materialized=True,
        blocked=False,
        block_reason=None,
        lead_id=lead.id,
        contact_id=row.id,
        provider_id=candidate.provider_id,
        contact_type=contact_type.value,
        created=True,
        already_present=False,
    )


def _find_exact(
    session: Session,
    lead_id: uuid.UUID,
    contact_type: ContactType,
    normalized: str,
) -> Contact | None:
    return session.scalar(
        select(Contact)
        .where(Contact.lead_id == lead_id)
        .where(Contact.contact_type == contact_type)
        .where(Contact.value == normalized)
        .limit(1)
    )


def _already_present(contact: Contact, *, provider_id: str | None) -> ContactMaterializeResult:
    logger.info(
        "contact_materialize_existing",
        lead_id=str(contact.lead_id),
        contact_id=str(contact.id),
        provider_id=provider_id,
        contact_type=contact.contact_type.value,
        created=False,
        already_present=True,
    )
    return ContactMaterializeResult(
        materialized=True,
        blocked=False,
        block_reason=None,
        lead_id=contact.lead_id,
        contact_id=contact.id,
        provider_id=provider_id,
        contact_type=contact.contact_type.value,
        created=False,
        already_present=True,
    )


def _normalize(candidate_type: ContactCandidateType, raw: str) -> str | None:
    if candidate_type is ContactCandidateType.PHONE:
        return normalize_phone(raw)
    if candidate_type is ContactCandidateType.EMAIL:
        return normalize_email(raw)
    return None


def _blocked(
    reason: MaterializeBlockReason,
    *,
    lead_id: uuid.UUID | None,
    provider_id: str | None,
    contact_type: str | None,
) -> ContactMaterializeResult:
    logger.info(
        "contact_materialize_blocked",
        lead_id=str(lead_id) if lead_id else None,
        provider_id=provider_id,
        contact_type=contact_type,
        block_reason=reason.value,
    )
    return ContactMaterializeResult(
        materialized=False,
        blocked=True,
        block_reason=reason,
        lead_id=lead_id,
        contact_id=None,
        provider_id=provider_id,
        contact_type=contact_type,
        created=False,
        already_present=False,
    )
