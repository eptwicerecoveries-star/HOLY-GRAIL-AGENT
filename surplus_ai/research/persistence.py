"""Append-only persistence of research outcomes into research_results."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy.orm import Session

from surplus_ai.database.models.enums import ResearchStatus
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.research.confidence import (
    asserts_legal_entitlement,
    is_ambiguous_identity_match,
    outcome_requires_human_review,
)
from surplus_ai.research.exceptions import ResearchPersistenceError
from surplus_ai.research.models import (
    PropertyLookupQuery,
    ProviderOutcome,
    ProviderOutcomeStatus,
    RequestPayload,
    ResearchKind,
    ResponsePayload,
)
from surplus_ai.research.provenance import redact_secrets
from surplus_ai.utils.hashing import stable_row_hash

logger = structlog.get_logger(__name__)


_STATUS_MAP: dict[ProviderOutcomeStatus, ResearchStatus] = {
    ProviderOutcomeStatus.SUCCESS: ResearchStatus.SUCCESS,
    ProviderOutcomeStatus.NOT_FOUND: ResearchStatus.NOT_FOUND,
    ProviderOutcomeStatus.ERROR: ResearchStatus.ERROR,
    ProviderOutcomeStatus.RATE_LIMITED: ResearchStatus.ERROR,
    ProviderOutcomeStatus.TIMEOUT: ResearchStatus.ERROR,
    ProviderOutcomeStatus.SKIPPED: ResearchStatus.NOT_FOUND,
}


class ResearchResultWriter:
    """Writes ResearchResult rows only. Never mutates leads, contacts, properties, or compliance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def persist(
        self,
        *,
        surplus_case_id: uuid.UUID,
        provider_name: str,
        query: PropertyLookupQuery,
        outcome: ProviderOutcome,
    ) -> ResearchResult:
        if asserts_legal_entitlement(outcome):
            raise ResearchPersistenceError(
                "Refusing to persist an outcome that asserts legal entitlement"
            )

        request = build_request_payload(provider_name=provider_name, query=query)

        try:
            response = build_response_payload(outcome)
            needs_review = outcome_requires_human_review(outcome) or is_ambiguous_identity_match(
                outcome.evidence
            )
            if needs_review and not response.requires_human_review:
                response = response.model_copy(update={"requires_human_review": True})
            db_status = _STATUS_MAP[outcome.status]
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "research_outcome_malformed",
                case_id=str(surplus_case_id),
                provider=provider_name,
                error=str(exc),
            )
            response = ResponsePayload(
                found=False,
                requires_human_review=True,
                error_code="malformed_provider_response",
                error_detail=str(exc),
                evidence=(),
                notes="Provider returned a malformed outcome; recorded as error.",
                provider_status=ProviderOutcomeStatus.ERROR,
                cacheable=False,
                raw_response=redact_secrets(outcome.raw_response),
            )
            db_status = ResearchStatus.ERROR

        if db_status is ResearchStatus.SUCCESS and not response.found:
            raise ResearchPersistenceError(
                "Refusing to store success without found=True in response payload"
            )

        row = ResearchResult(
            surplus_case_id=surplus_case_id,
            provider=provider_name,
            request_payload=_to_jsonable(request),
            response_payload=_to_jsonable(response),
            status=db_status,
        )
        self._session.add(row)
        self._session.flush()
        logger.info(
            "research_result_persisted",
            research_result_id=str(row.id),
            case_id=str(surplus_case_id),
            provider=provider_name,
            status=db_status.value,
        )
        return row


def build_request_payload(*, provider_name: str, query: PropertyLookupQuery) -> RequestPayload:
    cache_key = stable_row_hash(
        {
            "kind": ResearchKind.PROPERTY.value,
            "provider": provider_name,
            "state": query.state.upper(),
            "county": query.county_slug.lower(),
            "parcel_id": query.parcel_id or "",
            "owner": query.owner_raw_name or "",
            "address": query.property_address_raw or "",
        }
    )
    raw_query = redact_secrets(query.model_dump(mode="json")) or {}
    return RequestPayload(
        research_kind=ResearchKind.PROPERTY,
        cache_key=cache_key,
        county_state=query.state.upper(),
        county_slug=query.county_slug.lower(),
        parcel_id=query.parcel_id,
        owner_raw_name=query.owner_raw_name,
        property_address_raw=query.property_address_raw,
        query=raw_query,
        provider=provider_name,
    )


def persisted_cacheable(outcome: ProviderOutcome) -> bool:
    """Explicit persist flag: only SUCCESS/NOT_FOUND with cacheable=True become true."""
    if outcome.status not in (
        ProviderOutcomeStatus.SUCCESS,
        ProviderOutcomeStatus.NOT_FOUND,
    ):
        return False
    return outcome.cacheable is True


def build_response_payload(outcome: ProviderOutcome) -> ResponsePayload:
    return ResponsePayload(
        found=outcome.found,
        source_url=outcome.source_url,
        requires_human_review=outcome.requires_human_review
        or is_ambiguous_identity_match(outcome.evidence),
        error_code=outcome.error_code,
        error_detail=outcome.error_detail,
        evidence=outcome.evidence,
        notes=outcome.notes,
        provider_status=outcome.status,
        cacheable=persisted_cacheable(outcome),
        raw_response=redact_secrets(outcome.raw_response),
    )


def parse_outcome_dict(data: dict[str, Any]) -> ProviderOutcome:
    """Validate a fixture/dict into ProviderOutcome; raises ValidationError if malformed."""
    return ProviderOutcome.model_validate(data)


def _to_jsonable(model: RequestPayload | ResponsePayload) -> dict[str, Any]:
    return model.model_dump(mode="json")
