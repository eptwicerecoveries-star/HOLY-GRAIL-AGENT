from __future__ import annotations

import pytest
from pydantic import ValidationError

from surplus_ai.research.models import (
    PAYLOAD_SCHEMA_VERSION,
    EvidenceAtom,
    ProviderOutcome,
    ProviderOutcomeStatus,
    RequestPayload,
    ResearchMethod,
    ResponsePayload,
    utc_now,
)


def test_evidence_atom_requires_provenance_fields() -> None:
    atom = EvidenceAtom(
        field="mailing_address",
        original_value="1 MAIN",
        normalized_value="1 MAIN",
        source="fixture",
        source_url="https://example.invalid/x",
        retrieved_at=utc_now(),
        confidence=0.9,
        method=ResearchMethod.OPEN_DATA,
        requires_human_verification=False,
        owner_type_context="individual",
    )
    assert atom.source_url is not None
    assert atom.retrieved_at.tzinfo is not None


def test_provider_outcome_rejects_found_without_success() -> None:
    with pytest.raises(ValidationError):
        ProviderOutcome(
            status=ProviderOutcomeStatus.NOT_FOUND,
            found=True,
        )


def test_provider_outcome_rejects_success_without_found() -> None:
    with pytest.raises(ValidationError):
        ProviderOutcome(
            status=ProviderOutcomeStatus.SUCCESS,
            found=False,
        )


def test_payload_schema_version_is_one() -> None:
    req = RequestPayload(
        cache_key="abc",
        county_state="MD",
        county_slug="harford",
        provider="manual_lookup",
    )
    resp = ResponsePayload(
        found=False,
        requires_human_review=True,
        provider_status=ProviderOutcomeStatus.NOT_FOUND,
    )
    assert req.schema_version == PAYLOAD_SCHEMA_VERSION == 1
    assert resp.schema_version == 1


def test_response_payload_cacheable_defaults_none() -> None:
    resp = ResponsePayload(
        found=False,
        requires_human_review=True,
        provider_status=ProviderOutcomeStatus.NOT_FOUND,
    )
    assert resp.cacheable is None


def test_provider_outcome_retryable_defaults_false() -> None:
    outcome = ProviderOutcome(
        status=ProviderOutcomeStatus.ERROR,
        found=False,
        requires_human_review=True,
    )
    assert outcome.retryable is False
    assert outcome.cacheable is True
