"""Human research review queue. Evidence/workflow only — never entitlement."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    ResearchReviewReason,
    ResearchReviewResolution,
    ResearchStatus,
    ReviewStatus,
)
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.exceptions import ResearchReviewError
from surplus_ai.research.registry import ProviderRegistry

logger = structlog.get_logger(__name__)

_SAFE_TOKEN = re.compile(r"^[a-z0-9_]+$")
_COMPLEX_OWNER_TYPES = frozenset({"estate", "trust", "company", "government", "unknown"})
_IDENTITY_FIELDS = frozenset({"owner_name_on_record", "mailing_address", "current_address"})
_UNAVAILABLE_ERROR_CODES = frozenset(
    {
        "credentials_missing",
        "provider_not_configured",
        "provider_not_implemented",
        "automated_access_not_verified",
        "http_401",
        "http_403",
    }
)
_FAILURE_PROVIDER_STATUSES = frozenset({"error", "timeout", "rate_limited"})
_RESOLVE_RESOLUTIONS = frozenset(
    {
        ResearchReviewResolution.EVIDENCE_USABLE,
        ResearchReviewResolution.EVIDENCE_INSUFFICIENT,
        ResearchReviewResolution.NEEDS_ADDITIONAL_RESEARCH,
        ResearchReviewResolution.CONFLICT_UNRESOLVED,
    }
)
LOW_CONFIDENCE = 0.6

_REASON_DETAIL_TEXT = {
    ResearchReviewReason.AMBIGUOUS_IDENTITY: "Identity evidence is conflicting or low-confidence.",
    ResearchReviewReason.COMPLEX_OWNER_CONTEXT: "Owner-type context requires human inspection.",
    ResearchReviewReason.MANUAL_RESEARCH_REQUIRED: (
        "Configured provider type requires manual research."
    ),
    ResearchReviewReason.PROVIDER_UNAVAILABLE: (
        "Configured provider is unavailable or not implemented."
    ),
    ResearchReviewReason.PROVIDER_FAILURE: "Final persisted provider outcome is a failure.",
}


@dataclass(frozen=True)
class ReviewShowPayload:
    item: ResearchReviewItem
    triggering: ResearchResult
    newer: ResearchResult | None


def classify_review_reason(
    row: ResearchResult,
    *,
    provider_type: str | None,
) -> ResearchReviewReason:
    """First-match workflow reason. Never a legal conclusion."""
    payload = row.response_payload or {}
    if _payload_is_ambiguous(payload):
        return ResearchReviewReason.AMBIGUOUS_IDENTITY
    if _payload_has_complex_owner(payload):
        return ResearchReviewReason.COMPLEX_OWNER_CONTEXT
    if provider_type == "manual":
        return ResearchReviewReason.MANUAL_RESEARCH_REQUIRED
    if _is_provider_unavailable(row, provider_type):
        return ResearchReviewReason.PROVIDER_UNAVAILABLE
    if _is_provider_failure(row):
        return ResearchReviewReason.PROVIDER_FAILURE
    return ResearchReviewReason.MANUAL_RESEARCH_REQUIRED


def build_reason_detail(
    reason: ResearchReviewReason,
    row: ResearchResult,
) -> str:
    """Machine-generated detail from structured fields only. Never copies notes/raw."""
    payload = row.response_payload or {}
    parts = [
        f"reason={reason.value}",
        _REASON_DETAIL_TEXT[reason],
    ]
    provider_status = payload.get("provider_status")
    if isinstance(provider_status, str) and _SAFE_TOKEN.match(provider_status):
        parts.append(f"provider_status={provider_status}")
    error_code = payload.get("error_code")
    if isinstance(error_code, str) and _SAFE_TOKEN.match(error_code):
        parts.append(f"error_code={error_code}")
    return " ".join(parts)


def _payload_is_ambiguous(payload: dict[str, Any]) -> bool:
    evidence = payload.get("evidence") or []
    if not isinstance(evidence, list):
        return False
    identity = [a for a in evidence if isinstance(a, dict) and a.get("field") in _IDENTITY_FIELDS]
    if not identity:
        return False
    for atom in identity:
        confidence = atom.get("confidence")
        if isinstance(confidence, int | float) and float(confidence) < LOW_CONFIDENCE:
            return True
    by_field: dict[str, set[str]] = {}
    for atom in identity:
        field = atom.get("field")
        value = atom.get("normalized_value")
        if not isinstance(field, str) or not isinstance(value, str):
            continue
        by_field.setdefault(field, set()).add(value)
    return any(len(values) > 1 for values in by_field.values())


def _payload_has_complex_owner(payload: dict[str, Any]) -> bool:
    evidence = payload.get("evidence") or []
    if not isinstance(evidence, list):
        return False
    for atom in evidence:
        if not isinstance(atom, dict):
            continue
        context = atom.get("owner_type_context")
        if isinstance(context, str) and context.lower() in _COMPLEX_OWNER_TYPES:
            return True
    return False


def _is_provider_unavailable(row: ResearchResult, provider_type: str | None) -> bool:
    payload = row.response_payload or {}
    error_code = payload.get("error_code")
    if error_code in _UNAVAILABLE_ERROR_CODES:
        return True
    if payload.get("provider_status") == "skipped":
        return True
    return provider_type in {"null", "credentials_required"}


def _is_provider_failure(row: ResearchResult) -> bool:
    payload = row.response_payload or {}
    if row.status is ResearchStatus.ERROR:
        return True
    if payload.get("provider_status") in _FAILURE_PROVIDER_STATUSES:
        return True
    return payload.get("error_code") == "malformed_provider_response"


class ResearchReviewQueue:
    """Work list over persisted ResearchResult rows. Does not mutate evidence."""

    def __init__(self, session: Session, registry: ProviderRegistry | None = None) -> None:
        self._session = session
        self._registry = registry or ProviderRegistry()

    def enqueue_for_result(self, row: ResearchResult) -> ResearchReviewItem | None:
        """Create or reuse a pending item for a newly persisted result."""
        payload = row.response_payload or {}
        if payload.get("requires_human_review") is not True:
            return None

        provider_type = self._registry.provider_type(row.provider)
        reason = classify_review_reason(row, provider_type=provider_type)
        existing = self._find_pending(row.surplus_case_id, row.provider, reason)
        if existing is not None:
            logger.info(
                "research_review_deduped",
                review_item_id=str(existing.id),
                research_result_id=str(row.id),
                reason=reason.value,
            )
            return existing

        item = ResearchReviewItem(
            surplus_case_id=row.surplus_case_id,
            research_result_id=row.id,
            provider=row.provider,
            reason=reason,
            reason_detail=build_reason_detail(reason, row),
            status=ReviewStatus.PENDING,
        )
        try:
            with self._session.begin_nested():
                self._session.add(item)
                self._session.flush()
        except IntegrityError:
            recovered = self._find_pending(row.surplus_case_id, row.provider, reason)
            if recovered is None:
                raise
            logger.info(
                "research_review_deduped_race",
                review_item_id=str(recovered.id),
                research_result_id=str(row.id),
                reason=reason.value,
            )
            return recovered

        logger.info(
            "research_review_enqueued",
            review_item_id=str(item.id),
            research_result_id=str(row.id),
            reason=reason.value,
        )
        return item

    def get(self, item_id: uuid.UUID) -> ResearchReviewItem | None:
        return self._session.get(ResearchReviewItem, item_id)

    def list_items(
        self,
        *,
        status: ReviewStatus | None = ReviewStatus.PENDING,
        state: str | None = None,
        county_slug: str | None = None,
        provider: str | None = None,
        reason: ResearchReviewReason | None = None,
        case_id: uuid.UUID | None = None,
        limit: int = 20,
    ) -> tuple[int, list[ResearchReviewItem]]:
        if limit < 1:
            raise ResearchReviewError("limit must be >= 1")
        filters = self._filter_stmt(
            status=status,
            state=state,
            county_slug=county_slug,
            provider=provider,
            reason=reason,
            case_id=case_id,
        )
        total = self._session.scalar(select(func.count()).select_from(filters.subquery())) or 0
        rows = list(
            self._session.scalars(
                filters.order_by(ResearchReviewItem.created_at.asc()).limit(limit)
            ).all()
        )
        return total, rows

    def show(self, item_id: uuid.UUID) -> ReviewShowPayload:
        item = self.get(item_id)
        if item is None:
            raise ResearchReviewError(f"No research review item {item_id}")
        triggering = self._session.get(ResearchResult, item.research_result_id)
        if triggering is None:
            raise ResearchReviewError(
                f"Research review item {item_id} is missing its triggering result"
            )
        newer = self._newer_result(item, triggering)
        return ReviewShowPayload(item=item, triggering=triggering, newer=newer)

    def resolve(
        self,
        item_id: uuid.UUID,
        *,
        reviewed_by: str,
        notes: str = "",
        reject: bool = False,
        resolution: ResearchReviewResolution | None = None,
    ) -> ResearchReviewItem:
        reviewer = reviewed_by.strip()
        if not reviewer:
            raise ResearchReviewError("reviewed_by must be a non-empty string")

        if reject:
            if (
                resolution is not None
                and resolution is not ResearchReviewResolution.NOT_RELEVANT
            ):
                raise ResearchReviewError("--reject requires resolution not_relevant")
            new_status = ReviewStatus.REJECTED
            new_resolution = ResearchReviewResolution.NOT_RELEVANT
        else:
            if resolution is None:
                raise ResearchReviewError("resolution is required unless rejecting")
            if resolution not in _RESOLVE_RESOLUTIONS:
                raise ResearchReviewError(
                    f"resolution {resolution.value!r} is not allowed when resolving"
                )
            new_status = ReviewStatus.RESOLVED
            new_resolution = resolution

        now = datetime.now(tz=UTC)
        result = self._session.execute(
            update(ResearchReviewItem)
            .where(ResearchReviewItem.id == item_id)
            .where(ResearchReviewItem.status == ReviewStatus.PENDING)
            .values(
                status=new_status,
                resolution=new_resolution,
                reviewed_by=reviewer,
                reviewed_at=now,
                reviewer_notes=notes,
            )
        )
        updated = int(getattr(result, "rowcount", 0) or 0)
        if updated != 1:
            existing = self.get(item_id)
            if existing is None:
                raise ResearchReviewError(f"No research review item {item_id}")
            raise ResearchReviewError(
                f"Research review item {item_id} is already {existing.status.value}"
            )
        self._session.expire_all()
        closed = self.get(item_id)
        if closed is None:
            raise ResearchReviewError(f"No research review item {item_id}")
        logger.info(
            "research_review_closed",
            review_item_id=str(item_id),
            status=new_status.value,
            resolution=new_resolution.value,
        )
        return closed

    def _find_pending(
        self,
        surplus_case_id: uuid.UUID,
        provider: str,
        reason: ResearchReviewReason,
    ) -> ResearchReviewItem | None:
        return self._session.scalar(
            select(ResearchReviewItem)
            .where(ResearchReviewItem.surplus_case_id == surplus_case_id)
            .where(ResearchReviewItem.provider == provider)
            .where(ResearchReviewItem.reason == reason)
            .where(ResearchReviewItem.status == ReviewStatus.PENDING)
            .limit(1)
        )

    def _newer_result(
        self, item: ResearchReviewItem, triggering: ResearchResult
    ) -> ResearchResult | None:
        """Return the newest deterministic sibling at or after the triggering timestamp.

        ``fetched_at`` uses PostgreSQL ``now()`` (transaction timestamp), so two
        appends in one transaction can share a timestamp. ``id`` is a random UUID
        and is not insertion order. A distinct sibling with
        ``fetched_at >= triggering.fetched_at`` is selected by
        ``ORDER BY fetched_at DESC, id DESC``. The frozen ``research_result_id``
        is never retargeted.
        """
        return self._session.scalar(
            select(ResearchResult)
            .where(ResearchResult.surplus_case_id == item.surplus_case_id)
            .where(ResearchResult.provider == item.provider)
            .where(ResearchResult.id != triggering.id)
            .where(ResearchResult.fetched_at >= triggering.fetched_at)
            .order_by(ResearchResult.fetched_at.desc(), ResearchResult.id.desc())
            .limit(1)
        )

    def _filter_stmt(
        self,
        *,
        status: ReviewStatus | None,
        state: str | None,
        county_slug: str | None,
        provider: str | None,
        reason: ResearchReviewReason | None,
        case_id: uuid.UUID | None,
    ) -> Select[tuple[ResearchReviewItem]]:
        stmt = select(ResearchReviewItem)
        needs_county = bool(state or county_slug)
        if needs_county:
            stmt = stmt.join(
                SurplusCase, ResearchReviewItem.surplus_case_id == SurplusCase.id
            ).join(County, SurplusCase.county_id == County.id)
        if status is not None:
            stmt = stmt.where(ResearchReviewItem.status == status)
        if provider:
            stmt = stmt.where(ResearchReviewItem.provider == provider)
        if reason is not None:
            stmt = stmt.where(ResearchReviewItem.reason == reason)
        if case_id is not None:
            stmt = stmt.where(ResearchReviewItem.surplus_case_id == case_id)
        if state:
            stmt = stmt.where(County.state == state.strip().upper())
        if county_slug:
            stmt = stmt.where(County.slug == county_slug.strip().lower())
        return stmt
