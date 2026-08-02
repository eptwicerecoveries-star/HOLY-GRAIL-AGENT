from __future__ import annotations

from collections import Counter

import structlog

from surplus_ai.classifier.entity_keywords import EntityKeywordSet, load_entity_keywords
from surplus_ai.classifier.models import (
    ClassificationMethodName,
    ClassificationResult,
)
from surplus_ai.classifier.rules import RuleBasedClassifier, split_person_name
from surplus_ai.database.models.enums import OwnerType

logger = structlog.get_logger(__name__)

# A marker match is strong evidence: these suffixes exist precisely to name a legal form.
CONFIDENCE_MARKER_MATCH = 0.95

# A person is inferred from shape rather than from a positive signal, so the ceiling is
# lower. An unfamiliar organisation with no recognisable suffix looks like a person.
CONFIDENCE_PERSON_SHAPE = 0.80

# Nothing matched and the shape said nothing either. Recorded, never acted on.
CONFIDENCE_UNKNOWN = 0.0


class OwnerTypeClassifier:
    """Decides whether an owner is a person, a company, an estate, a trust or the state.

    The business asks to remove companies and keep individuals. Estates and trusts are
    kept as their own answers rather than folded into either, because an estate is a lead:
    the owner has died, the heirs are entitled to the money, and they are often unaware it
    exists. Whether each kind is pursued is configuration, since that is a commercial
    decision rather than a fact about the name.

    Nothing is ever discarded here. A name that cannot be read -- and recognition leaves
    plenty, like a cell holding only "&" -- becomes UNKNOWN at zero confidence, which keeps
    it out of a call list without deleting the record.
    """

    def __init__(self, keywords: EntityKeywordSet | None = None) -> None:
        self._keywords = keywords or load_entity_keywords()
        self._rules = RuleBasedClassifier(self._keywords)

    def classify(self, raw_name: str) -> ClassificationResult:
        """Classify one owner name."""
        owner_type, matched = self._rules.classify_type(raw_name)
        confidence, evidence = self._score(owner_type, matched)

        person_name = None
        if owner_type is OwnerType.INDIVIDUAL:
            person_name = split_person_name(raw_name, self._keywords)

        result = ClassificationResult(
            raw_name=raw_name,
            owner_type=owner_type,
            confidence=confidence,
            method=ClassificationMethodName.RULE,
            evidence=evidence,
            matched_markers=matched,
            person_name=person_name,
            is_pursuable=self._keywords.is_pursuable(owner_type),
        )
        logger.debug(
            "owner_classified",
            owner_type=owner_type.value,
            confidence=confidence,
            pursuable=result.is_pursuable,
        )
        return result

    def classify_many(self, names: list[str]) -> list[ClassificationResult]:
        return [self.classify(name) for name in names]

    def _score(self, owner_type: OwnerType, matched: tuple[str, ...]) -> tuple[float, str]:
        if owner_type is OwnerType.UNKNOWN:
            return CONFIDENCE_UNKNOWN, (
                "No entity marker matched and the name does not have the shape of a "
                "person. Recorded but not actionable."
            )
        if matched:
            return CONFIDENCE_MARKER_MATCH, (
                f"Matched {owner_type.value} marker(s): {', '.join(matched)}."
            )
        return CONFIDENCE_PERSON_SHAPE, (
            "No organisation marker matched and the name has the shape of a person. "
            "Inferred from shape, so an unfamiliar legal form could still be misread."
        )


class ClassificationSummary:
    """Counts across a batch, for reporting what a county's list actually contains."""

    def __init__(self, results: list[ClassificationResult]) -> None:
        self.results = results
        self.counts: Counter[OwnerType] = Counter(r.owner_type for r in results)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def pursuable(self) -> list[ClassificationResult]:
        return [r for r in self.results if r.is_pursuable]

    @property
    def excluded(self) -> list[ClassificationResult]:
        return [r for r in self.results if not r.is_pursuable]

    def share(self, owner_type: OwnerType) -> float:
        return self.counts[owner_type] / self.total if self.total else 0.0
