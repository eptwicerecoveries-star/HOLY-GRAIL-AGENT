"""Confidence helpers for research evidence.

These flags support human review. They never declare legal entitlement, claimancy,
or contact eligibility — compliance and leads remain authoritative for those decisions.
"""

from __future__ import annotations

from surplus_ai.research.models import EvidenceAtom, ProviderOutcome, ProviderOutcomeStatus

# Thresholds for Phase 6A (config file for thresholds deferred to later phases).
HIGH_CONFIDENCE = 0.9
LOW_CONFIDENCE = 0.6

_ALWAYS_REVIEW_OWNER_TYPES = frozenset({"estate", "trust", "company", "government", "unknown"})


def evidence_requires_human_review(atom: EvidenceAtom) -> bool:
    """Whether a single evidence atom should be reviewed by a person."""
    if atom.requires_human_verification:
        return True
    if atom.confidence < LOW_CONFIDENCE:
        return True
    if atom.owner_type_context and atom.owner_type_context.lower() in _ALWAYS_REVIEW_OWNER_TYPES:
        return True
    return False


def outcome_requires_human_review(outcome: ProviderOutcome) -> bool:
    """Aggregate review flag for a provider outcome."""
    if outcome.requires_human_review:
        return True
    if outcome.status is not ProviderOutcomeStatus.SUCCESS:
        # Manual / missing-config paths often already set the flag; keep explicit.
        return outcome.requires_human_review
    return any(evidence_requires_human_review(atom) for atom in outcome.evidence)


def is_ambiguous_identity_match(evidence: tuple[EvidenceAtom, ...] | list[EvidenceAtom]) -> bool:
    """True when multiple identity-like values conflict or confidence is low.

    Ambiguous matches must go to human review. This never asserts who the legal claimant is.
    """
    identity_fields = {"owner_name_on_record", "mailing_address", "current_address"}
    identity_atoms = [a for a in evidence if a.field in identity_fields]
    if not identity_atoms:
        return False
    if any(a.confidence < LOW_CONFIDENCE for a in identity_atoms):
        return True
    # Distinct normalized values for the same field → conflict / ambiguity.
    by_field: dict[str, set[str]] = {}
    for atom in identity_atoms:
        if atom.normalized_value is None:
            continue
        by_field.setdefault(atom.field, set()).add(atom.normalized_value)
    return any(len(values) > 1 for values in by_field.values())


def asserts_legal_entitlement(_outcome: ProviderOutcome) -> bool:
    """Phase 6A always returns False: research never asserts legal entitlement.

    Kept as an explicit API so tests can lock the boundary.
    """
    return False
