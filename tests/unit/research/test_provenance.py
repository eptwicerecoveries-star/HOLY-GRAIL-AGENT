from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from surplus_ai.research.models import ResearchMethod
from surplus_ai.research.persistence import parse_outcome_dict
from surplus_ai.research.provenance import (
    build_evidence_atom,
    evidence_has_required_provenance,
    redact_secrets,
)


def test_build_evidence_atom_preserves_provenance() -> None:
    atom = build_evidence_atom(
        field="mailing_address",
        original_value="123 MAIN ST",
        normalized_value="123 MAIN ST",
        source="fixture_open_data",
        method=ResearchMethod.OPEN_DATA,
        confidence=0.95,
        requires_human_verification=False,
        source_url="https://example.invalid/p",
        owner_type_context="individual",
    )
    assert evidence_has_required_provenance(atom)
    assert atom.original_value == "123 MAIN ST"
    assert atom.normalized_value == "123 MAIN ST"
    assert atom.source == "fixture_open_data"
    assert atom.source_url == "https://example.invalid/p"
    assert atom.retrieved_at is not None
    assert atom.method is ResearchMethod.OPEN_DATA


def test_redact_secrets_removes_credential_keys() -> None:
    redacted = redact_secrets(
        {
            "api_key": "SECRET",
            "Authorization": "Bearer x",
            "nested": {"password": "p", "parcel": "1"},
            "ok": "value",
        }
    )
    assert redacted is not None
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["parcel"] == "1"
    assert redacted["ok"] == "value"


def test_parse_success_fixture(outcome_fixtures: dict[str, Any]) -> None:
    outcome = parse_outcome_dict(outcome_fixtures["success"])
    assert outcome.found is True
    assert len(outcome.evidence) == 1
    assert evidence_has_required_provenance(outcome.evidence[0])


def test_malformed_fixture_rejected(outcome_fixtures: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse_outcome_dict(outcome_fixtures["malformed_missing_status"])
    with pytest.raises(ValidationError):
        parse_outcome_dict(outcome_fixtures["malformed_found_without_success"])
