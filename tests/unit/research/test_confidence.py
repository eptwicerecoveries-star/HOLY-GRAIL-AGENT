from __future__ import annotations

from typing import Any

from surplus_ai.research.confidence import (
    asserts_legal_entitlement,
    evidence_requires_human_review,
    is_ambiguous_identity_match,
    outcome_requires_human_review,
)
from surplus_ai.research.models import ResearchMethod, utc_now
from surplus_ai.research.persistence import parse_outcome_dict
from surplus_ai.research.provenance import build_evidence_atom


def test_never_asserts_legal_entitlement(outcome_fixtures: dict[str, Any]) -> None:
    outcome = parse_outcome_dict(outcome_fixtures["success"])
    assert asserts_legal_entitlement(outcome) is False


def test_low_confidence_requires_review() -> None:
    atom = build_evidence_atom(
        field="owner_name_on_record",
        original_value="A",
        normalized_value="A",
        source="fixture",
        method=ResearchMethod.OPEN_DATA,
        confidence=0.4,
        requires_human_verification=False,
        retrieved_at=utc_now(),
    )
    assert evidence_requires_human_review(atom) is True


def test_estate_context_requires_review() -> None:
    atom = build_evidence_atom(
        field="owner_name_on_record",
        original_value="ESTATE OF X",
        normalized_value="ESTATE OF X",
        source="fixture",
        method=ResearchMethod.OPEN_DATA,
        confidence=0.95,
        requires_human_verification=False,
        owner_type_context="estate",
        retrieved_at=utc_now(),
    )
    assert evidence_requires_human_review(atom) is True


def test_ambiguous_identity_from_fixture(outcome_fixtures: dict[str, Any]) -> None:
    outcome = parse_outcome_dict(outcome_fixtures["ambiguous_identity"])
    assert is_ambiguous_identity_match(outcome.evidence) is True
    assert outcome_requires_human_review(outcome) is True
