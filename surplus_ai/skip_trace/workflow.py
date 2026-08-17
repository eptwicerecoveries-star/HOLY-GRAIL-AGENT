"""Phase 6F-B: explicit Lead-bound offline skip-trace orchestration.

Consumes an existing Lead, invokes one supplied offline provider once, and routes
eligible candidates through Phase 6F-A ``materialize_contact_candidate``.

Does not create Lead, mutate Owner, change Compliance, send outreach, or call live
people-search vendors. Caller owns the transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

import structlog
from sqlalchemy.orm import Session

from surplus_ai.database.models.lead import Lead
from surplus_ai.skip_trace.materialize import (
    ContactMaterializeResult,
    materialize_contact_candidate,
)
from surplus_ai.skip_trace.models import SkipTraceStatus
from surplus_ai.skip_trace.providers.base import AbstractSkipTraceProvider

logger = structlog.get_logger(__name__)


class SkipTraceWorkflowBlockReason(str, Enum):
    LEAD_NOT_FOUND = "lead_not_found"
    PROVIDER_HUMAN_REVIEW_REQUIRED = "provider_human_review_required"
    PROVIDER_NOT_SUCCESS = "provider_not_success"
    PROVIDER_NO_CANDIDATES = "provider_no_candidates"


@dataclass(frozen=True)
class SkipTraceWorkflowResult:
    """Aggregate report — IDs and counts only; never phone/email values."""

    completed: bool
    blocked: bool
    block_reason: SkipTraceWorkflowBlockReason | None
    lead_id: uuid.UUID
    surplus_case_id: uuid.UUID | None
    provider_id: str | None
    provider_status: str | None
    provider_error_code: str | None
    provider_invoked: bool
    provider_candidate_count: int
    materialization_attempt_count: int
    created_count: int
    already_present_count: int
    blocked_count: int
    contact_ids: tuple[uuid.UUID, ...]
    materialization_results: tuple[ContactMaterializeResult, ...]


def run_skip_trace_for_lead(
    session: Session,
    *,
    lead_id: uuid.UUID,
    provider: AbstractSkipTraceProvider,
) -> SkipTraceWorkflowResult:
    """Run one offline skip-trace lookup for an existing Lead, then materialize.

    Lead gate runs before provider invocation. Provider is called at most once.
    Candidate→Contact authority remains ``materialize_contact_candidate``.
    """
    lead = session.get(Lead, lead_id)
    if lead is None:
        result = SkipTraceWorkflowResult(
            completed=False,
            blocked=True,
            block_reason=SkipTraceWorkflowBlockReason.LEAD_NOT_FOUND,
            lead_id=lead_id,
            surplus_case_id=None,
            provider_id=getattr(provider, "name", None),
            provider_status=None,
            provider_error_code=None,
            provider_invoked=False,
            provider_candidate_count=0,
            materialization_attempt_count=0,
            created_count=0,
            already_present_count=0,
            blocked_count=0,
            contact_ids=(),
            materialization_results=(),
        )
        _log_workflow(result)
        return result

    lookup = provider.lookup_contact_candidates(lead)
    provider_id = lookup.provider_id
    candidate_count = len(lookup.candidates)

    if lookup.requires_human_review:
        result = _provider_gate(
            lead=lead,
            provider_id=provider_id,
            provider_status=lookup.status.value,
            provider_error_code=lookup.error_code,
            provider_candidate_count=candidate_count,
            block_reason=SkipTraceWorkflowBlockReason.PROVIDER_HUMAN_REVIEW_REQUIRED,
        )
        _log_workflow(result)
        return result

    if lookup.status is not SkipTraceStatus.SUCCESS:
        result = _provider_gate(
            lead=lead,
            provider_id=provider_id,
            provider_status=lookup.status.value,
            provider_error_code=lookup.error_code,
            provider_candidate_count=candidate_count,
            block_reason=SkipTraceWorkflowBlockReason.PROVIDER_NOT_SUCCESS,
        )
        _log_workflow(result)
        return result

    if candidate_count == 0:
        result = _provider_gate(
            lead=lead,
            provider_id=provider_id,
            provider_status=lookup.status.value,
            provider_error_code=lookup.error_code,
            provider_candidate_count=0,
            block_reason=SkipTraceWorkflowBlockReason.PROVIDER_NO_CANDIDATES,
        )
        _log_workflow(result)
        return result

    materialization_results: list[ContactMaterializeResult] = []
    created = 0
    already_present = 0
    blocked = 0
    contact_ids: list[uuid.UUID] = []

    for candidate in lookup.candidates:
        item = materialize_contact_candidate(
            session,
            lead_id=lead.id,
            candidate=candidate,
        )
        materialization_results.append(item)
        if item.blocked:
            blocked += 1
            continue
        if item.created:
            created += 1
        elif item.already_present:
            already_present += 1
        if item.contact_id is not None:
            contact_ids.append(item.contact_id)

    result = SkipTraceWorkflowResult(
        completed=True,
        blocked=False,
        block_reason=None,
        lead_id=lead.id,
        surplus_case_id=lead.surplus_case_id,
        provider_id=provider_id,
        provider_status=lookup.status.value,
        provider_error_code=lookup.error_code,
        provider_invoked=True,
        provider_candidate_count=candidate_count,
        materialization_attempt_count=len(materialization_results),
        created_count=created,
        already_present_count=already_present,
        blocked_count=blocked,
        contact_ids=tuple(contact_ids),
        materialization_results=tuple(materialization_results),
    )
    _log_workflow(result)
    return result


def _provider_gate(
    *,
    lead: Lead,
    provider_id: str,
    provider_status: str,
    provider_error_code: str | None,
    provider_candidate_count: int,
    block_reason: SkipTraceWorkflowBlockReason,
) -> SkipTraceWorkflowResult:
    """Provider invoked; no candidate materialization attempted."""
    return SkipTraceWorkflowResult(
        completed=True,
        blocked=True,
        block_reason=block_reason,
        lead_id=lead.id,
        surplus_case_id=lead.surplus_case_id,
        provider_id=provider_id,
        provider_status=provider_status,
        provider_error_code=provider_error_code,
        provider_invoked=True,
        provider_candidate_count=provider_candidate_count,
        materialization_attempt_count=0,
        created_count=0,
        already_present_count=0,
        blocked_count=0,
        contact_ids=(),
        materialization_results=(),
    )


def _log_workflow(result: SkipTraceWorkflowResult) -> None:
    logger.info(
        "skip_trace_workflow",
        lead_id=str(result.lead_id),
        surplus_case_id=str(result.surplus_case_id) if result.surplus_case_id else None,
        provider_id=result.provider_id,
        provider_status=result.provider_status,
        provider_error_code=result.provider_error_code,
        provider_invoked=result.provider_invoked,
        provider_candidate_count=result.provider_candidate_count,
        materialization_attempt_count=result.materialization_attempt_count,
        created_count=result.created_count,
        already_present_count=result.already_present_count,
        blocked_count=result.blocked_count,
        blocked=result.blocked,
        block_reason=result.block_reason.value if result.block_reason else None,
        completed=result.completed,
    )
