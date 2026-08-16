"""Phase 6E-A: apply vetted ResearchResult evidence to local case/property fields.

Explicit service only (no pipeline/review hook). Fill-missing only. Research evidence
is not legal entitlement, claimant determination, contact authorization, or compliance
approval. Providers remain collectors only. Does not create Property rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models.enums import (
    ResearchReviewResolution,
    ResearchStatus,
    ReviewStatus,
)
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.mapping import match_normalize

logger = structlog.get_logger(__name__)

# Evidence fields with a safe local target in V1 (no migration; no Owner overwrite).
_SUPPORTED_EVIDENCE = frozenset({"parcel_id", "current_address"})
# Known research fields that deliberately have no safe Owner/Property/Case column yet.
_KNOWN_UNSUPPORTED = frozenset(
    {
        "account_id",
        "owner_name_on_record",
        "mailing_address",
        "property_record_id",
    }
)
_PROPERTY_PARCEL_TARGET = "property.parcel_id"


class EnrichmentBlockReason(str, Enum):
    """Stable apply-block codes. Field names only in reports — never PII values."""

    RESULT_NOT_FOUND = "result_not_found"
    CASE_RESULT_MISMATCH = "case_result_mismatch"
    RESULT_NOT_SUCCESSFUL = "result_not_successful"
    RESULT_NOT_FOUND_OUTCOME = "result_not_found_outcome"
    RESULT_HAS_NO_EVIDENCE = "result_has_no_evidence"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    HUMAN_REVIEW_NOT_USABLE = "human_review_not_usable"


@dataclass(frozen=True)
class EnrichmentApplyResult:
    """Deterministic apply report. Field *names* only — never owner/address values."""

    applied: bool
    blocked: bool
    block_reason: EnrichmentBlockReason | None
    case_id: uuid.UUID | None
    research_result_id: uuid.UUID | None
    provider_id: str | None
    applied_fields: tuple[str, ...] = ()
    already_present_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()
    skipped_empty_fields: tuple[str, ...] = ()
    conflicting_evidence_fields: tuple[str, ...] = ()


@dataclass
class _ApplyScratch:
    applied: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    skipped_empty: list[str] = field(default_factory=list)
    conflicting_evidence: list[str] = field(default_factory=list)
    property_parcel_filled: bool = False


class ResearchEnrichmentApplicator:
    """Apply one persisted ResearchResult onto its SurplusCase (fill-missing only)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def apply_research_result(
        self,
        *,
        case_id: uuid.UUID,
        research_result_id: uuid.UUID,
    ) -> EnrichmentApplyResult:
        row = self._session.get(ResearchResult, research_result_id)
        if row is None:
            return self._blocked(
                EnrichmentBlockReason.RESULT_NOT_FOUND,
                case_id=case_id,
                research_result_id=research_result_id,
                provider_id=None,
            )
        if row.surplus_case_id != case_id:
            return self._blocked(
                EnrichmentBlockReason.CASE_RESULT_MISMATCH,
                case_id=case_id,
                research_result_id=research_result_id,
                provider_id=row.provider,
            )

        case = self._session.get(SurplusCase, case_id)
        if case is None:
            return self._blocked(
                EnrichmentBlockReason.CASE_RESULT_MISMATCH,
                case_id=case_id,
                research_result_id=research_result_id,
                provider_id=row.provider,
            )

        payload = row.response_payload if isinstance(row.response_payload, dict) else {}
        gate = self._eligibility(row, payload)
        if gate is not None:
            return self._blocked(
                gate,
                case_id=case_id,
                research_result_id=research_result_id,
                provider_id=row.provider,
            )

        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list) or not evidence:
            return self._blocked(
                EnrichmentBlockReason.RESULT_HAS_NO_EVIDENCE,
                case_id=case_id,
                research_result_id=research_result_id,
                provider_id=row.provider,
            )

        by_field = self._collapse_evidence(evidence)
        scratch = _ApplyScratch()
        for field_name, values in by_field.items():
            if field_name in _KNOWN_UNSUPPORTED or field_name not in _SUPPORTED_EVIDENCE:
                if field_name not in scratch.unsupported:
                    scratch.unsupported.append(field_name)
                continue
            if values is None:
                scratch.conflicting_evidence.append(field_name)
                continue
            if values == "":
                scratch.skipped_empty.append(field_name)
                continue
            if field_name == "parcel_id":
                self._apply_parcel(case, values, scratch)
            elif field_name == "current_address":
                self._apply_current_address(case, values, scratch)

        if scratch.property_parcel_filled:
            prop = self._property_for(case.id)
            if prop is not None:
                prop.last_researched_at = datetime.now(UTC)

        self._session.flush()
        applied = bool(scratch.applied)
        logger.info(
            "research_enrichment_applied",
            case_id=str(case_id),
            research_result_id=str(research_result_id),
            provider_id=row.provider,
            applied=applied,
            applied_fields=list(scratch.applied),
            already_present_fields=list(scratch.already_present),
            conflicting_fields=list(scratch.conflicting),
            unsupported_fields=list(scratch.unsupported),
            skipped_empty_fields=list(scratch.skipped_empty),
            conflicting_evidence_fields=list(scratch.conflicting_evidence),
        )
        return EnrichmentApplyResult(
            applied=applied,
            blocked=False,
            block_reason=None,
            case_id=case_id,
            research_result_id=research_result_id,
            provider_id=row.provider,
            applied_fields=tuple(scratch.applied),
            already_present_fields=tuple(scratch.already_present),
            conflicting_fields=tuple(scratch.conflicting),
            unsupported_fields=tuple(scratch.unsupported),
            skipped_empty_fields=tuple(scratch.skipped_empty),
            conflicting_evidence_fields=tuple(scratch.conflicting_evidence),
        )

    def _eligibility(
        self, row: ResearchResult, payload: dict[str, Any]
    ) -> EnrichmentBlockReason | None:
        if row.status is not ResearchStatus.SUCCESS:
            return EnrichmentBlockReason.RESULT_NOT_SUCCESSFUL
        if payload.get("found") is not True:
            return EnrichmentBlockReason.RESULT_NOT_FOUND_OUTCOME
        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list) or len(evidence) == 0:
            return EnrichmentBlockReason.RESULT_HAS_NO_EVIDENCE
        if payload.get("requires_human_review") is True:
            return self._review_gate(row.id)
        return None

    def _review_gate(self, research_result_id: uuid.UUID) -> EnrichmentBlockReason | None:
        """Fail-closed aggregate for items frozen to this ResearchResult.

        Pending items block. Any non-``evidence_usable`` closed/rejected resolution blocks.
        At least one ``resolved`` + ``evidence_usable`` item is required.
        """
        items = list(
            self._session.scalars(
                select(ResearchReviewItem).where(
                    ResearchReviewItem.research_result_id == research_result_id
                )
            ).all()
        )
        if not items:
            return EnrichmentBlockReason.HUMAN_REVIEW_REQUIRED
        if any(item.status is ReviewStatus.PENDING for item in items):
            return EnrichmentBlockReason.HUMAN_REVIEW_REQUIRED
        usable = False
        for item in items:
            if item.resolution is ResearchReviewResolution.EVIDENCE_USABLE:
                if item.status is ReviewStatus.RESOLVED:
                    usable = True
                continue
            return EnrichmentBlockReason.HUMAN_REVIEW_NOT_USABLE
        if not usable:
            return EnrichmentBlockReason.HUMAN_REVIEW_NOT_USABLE
        return None

    def _collapse_evidence(
        self, evidence: list[object]
    ) -> dict[str, str | None]:
        """Map field → value, or None when same-field atoms conflict.

        Empty string means all atoms for the field were empty/whitespace.
        """
        buckets: dict[str, list[str]] = {}
        for atom in evidence:
            if not isinstance(atom, dict):
                continue
            name = atom.get("field")
            if not isinstance(name, str) or not name:
                continue
            raw = atom.get("original_value")
            if raw is None:
                text = ""
            elif isinstance(raw, str):
                text = raw.strip()
            else:
                buckets.setdefault(name, []).append("__non_scalar__")
                continue
            buckets.setdefault(name, []).append(text)

        collapsed: dict[str, str | None] = {}
        for name, values in buckets.items():
            nonzero = [v for v in values if v and v != "__non_scalar__"]
            if any(v == "__non_scalar__" for v in values):
                collapsed[name] = None
                continue
            if not nonzero:
                collapsed[name] = ""
                continue
            unique = {v for v in nonzero}
            if len(unique) > 1:
                collapsed[name] = None
            else:
                collapsed[name] = next(iter(unique))
        return collapsed

    def _apply_parcel(self, case: SurplusCase, value: str, scratch: _ApplyScratch) -> None:
        target_case = "surplus_case.parcel_id"
        outcome = self._fill_text(
            current=case.parcel_id,
            incoming=value,
            compare=match_normalize,
        )
        if outcome == "applied":
            case.parcel_id = value
            scratch.applied.append(target_case)
        elif outcome == "already":
            scratch.already_present.append(target_case)
        else:
            scratch.conflicting.append(target_case)
        self._apply_existing_property_parcel(case, value, scratch)

    def _apply_existing_property_parcel(
        self, case: SurplusCase, value: str, scratch: _ApplyScratch
    ) -> None:
        """Fill an existing Property only. Never create Property rows."""
        prop = self._property_for(case.id)
        target = _PROPERTY_PARCEL_TARGET
        if prop is None:
            if target not in scratch.unsupported:
                scratch.unsupported.append(target)
            return
        outcome = self._fill_text(current=prop.parcel_id, incoming=value, compare=match_normalize)
        if outcome == "applied":
            prop.parcel_id = value
            scratch.applied.append(target)
            scratch.property_parcel_filled = True
        elif outcome == "already":
            if target not in scratch.already_present:
                scratch.already_present.append(target)
        else:
            if target not in scratch.conflicting:
                scratch.conflicting.append(target)

    def _apply_current_address(
        self, case: SurplusCase, value: str, scratch: _ApplyScratch
    ) -> None:
        target = "surplus_case.property_address_raw"
        outcome = self._fill_text(
            current=case.property_address_raw,
            incoming=value,
            compare=lambda s: " ".join(s.strip().upper().split()) if s else "",
        )
        if outcome == "applied":
            case.property_address_raw = value
            scratch.applied.append(target)
        elif outcome == "already":
            scratch.already_present.append(target)
        else:
            scratch.conflicting.append(target)

    def _property_for(self, case_id: uuid.UUID) -> Property | None:
        return self._session.scalar(select(Property).where(Property.surplus_case_id == case_id))

    @staticmethod
    def _fill_text(
        *,
        current: str | None,
        incoming: str,
        compare: Any,
    ) -> str:
        cur = (current or "").strip()
        if not cur:
            return "applied"
        if compare(cur) == compare(incoming):
            return "already"
        return "conflict"

    @staticmethod
    def _blocked(
        reason: EnrichmentBlockReason,
        *,
        case_id: uuid.UUID | None,
        research_result_id: uuid.UUID | None,
        provider_id: str | None,
    ) -> EnrichmentApplyResult:
        logger.info(
            "research_enrichment_blocked",
            case_id=str(case_id) if case_id else None,
            research_result_id=str(research_result_id) if research_result_id else None,
            provider_id=provider_id,
            block_reason=reason.value,
        )
        return EnrichmentApplyResult(
            applied=False,
            blocked=True,
            block_reason=reason,
            case_id=case_id,
            research_result_id=research_result_id,
            provider_id=provider_id,
        )


def apply_research_result(
    session: Session,
    *,
    case_id: uuid.UUID,
    research_result_id: uuid.UUID,
) -> EnrichmentApplyResult:
    """Module-level entry point. Caller owns the transaction/commit."""
    return ResearchEnrichmentApplicator(session).apply_research_result(
        case_id=case_id,
        research_result_id=research_result_id,
    )
