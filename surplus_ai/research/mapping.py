"""Deterministic parcel/account matching and evidence-atom construction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime

from surplus_ai.research.endpoint import parse_socrata_number_identity
from surplus_ai.research.models import EvidenceAtom, ResearchMethod
from surplus_ai.research.provenance import build_evidence_atom

_STRIP_PUNCT = re.compile(r"[\s\-]+")

EXACT_MATCH_CONFIDENCE = 0.9
AMBIGUOUS_MATCH_CONFIDENCE = 0.45
OWNER_MISMATCH_CONFIDENCE = 0.7


def match_normalize(value: str | None) -> str:
    """Comparison-only identity normalize. Does not replace stored originals."""
    if value is None:
        return ""
    return _STRIP_PUNCT.sub("", value.strip()).upper()


def owner_normalize(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().upper().split())


def filter_identity_matches(
    rows: Sequence[Mapping[str, object]],
    *,
    field: str,
    expected: str,
    numeric: bool = False,
) -> list[Mapping[str, object]]:
    if numeric:
        return _filter_numeric_identity_matches(rows, field=field, expected=expected)
    wanted = match_normalize(expected)
    matched: list[Mapping[str, object]] = []
    for row in rows:
        raw = row.get(field)
        if raw is None:
            continue
        if match_normalize(str(raw)) == wanted:
            matched.append(row)
    return matched


def _filter_numeric_identity_matches(
    rows: Sequence[Mapping[str, object]],
    *,
    field: str,
    expected: str,
) -> list[Mapping[str, object]]:
    wanted = parse_socrata_number_identity(expected)
    if wanted is None:
        return []
    matched: list[Mapping[str, object]] = []
    for row in rows:
        raw = row.get(field)
        if raw is None:
            continue
        parsed = parse_socrata_number_identity(raw)
        if parsed is None:
            continue
        if parsed == wanted:
            matched.append(row)
    return matched


def atom(
    *,
    field: str,
    original_value: str | None,
    source: str,
    source_url: str,
    retrieved_at: datetime,
    confidence: float,
    requires_human_verification: bool,
    method: ResearchMethod = ResearchMethod.OPEN_DATA,
    owner_type_context: str | None = None,
) -> EvidenceAtom:
    normalized = original_value
    if field in {"parcel_id", "account_id", "property_record_id"}:
        normalized = match_normalize(original_value) or None
    elif field == "owner_name_on_record":
        normalized = owner_normalize(original_value) or None
    return build_evidence_atom(
        field=field,
        original_value=original_value,
        normalized_value=normalized,
        source=source,
        method=method,
        confidence=confidence,
        requires_human_verification=requires_human_verification,
        source_url=source_url,
        retrieved_at=retrieved_at,
        owner_type_context=owner_type_context,
    )
