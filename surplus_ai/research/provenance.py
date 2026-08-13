"""Provenance helpers: build evidence atoms and redact secrets from payloads."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, cast

from surplus_ai.research.models import (
    EvidenceAtom,
    ResearchMethod,
    utc_now,
)

_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|password|secret|token|credential|bearer)",
    re.IGNORECASE,
)


def build_evidence_atom(
    *,
    field: str,
    original_value: str | None,
    normalized_value: str | None,
    source: str,
    method: ResearchMethod,
    confidence: float,
    requires_human_verification: bool,
    source_url: str | None = None,
    retrieved_at: datetime | None = None,
    owner_type_context: str | None = None,
) -> EvidenceAtom:
    """Construct a fully provenance-bearing evidence atom."""
    return EvidenceAtom(
        field=field,
        original_value=original_value,
        normalized_value=normalized_value,
        source=source,
        source_url=source_url,
        retrieved_at=retrieved_at or utc_now(),
        confidence=confidence,
        method=method,
        requires_human_verification=requires_human_verification,
        owner_type_context=owner_type_context,
    )


def redact_secrets(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of *payload* with secret-looking keys replaced.

    Research payloads and fixtures must never store API credentials.
    """
    if payload is None:
        return None
    return cast(dict[str, Any], _redact_value(payload))


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_PATTERN.search(str(key)):
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = _redact_value(item)
        return out
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def evidence_has_required_provenance(atom: EvidenceAtom) -> bool:
    """True when every required provenance field is present on the atom."""
    return bool(
        atom.field
        and atom.source
        and atom.retrieved_at is not None
        and atom.method is not None
        and 0.0 <= atom.confidence <= 1.0
        and isinstance(atom.requires_human_verification, bool)
        # original_value / normalized_value may be None when the source reported absence,
        # but the fields themselves must exist on the model (guaranteed by EvidenceAtom).
    )
