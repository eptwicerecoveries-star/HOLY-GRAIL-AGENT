from __future__ import annotations

from typing import Any

from surplus_ai.database.models.enums import ExtractionMethod
from surplus_ai.parser.interpretation.alias_registry import load_confidence_config
from surplus_ai.parser.interpretation.models import ColumnMapping, RoutingDecision


class ConfidenceModel:
    """Combines every stage's confidence into one score, and decides what may be automated.

    The score is a weighted mean of the stages that had to be right for a row to mean what
    it says: how the document was classified, how well the table extracted, how sure the
    header was, and how well the columns resolved.

    Routing never removes anything. A low score means "a person should look at this", and
    the row is stored either way.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = config or load_confidence_config()
        self._weights: dict[str, float] = {str(k): float(v) for k, v in raw["weights"].items()}
        routing = raw["routing"]
        self._auto_accept = float(routing["auto_accept"])
        self._review = float(routing["review"])
        self._ocr_caps_at_review = bool(raw.get("ocr_caps_routing_at_review", True))

    def score(
        self,
        document_classification: float,
        extraction_quality: float,
        header_confidence: float,
        mappings: tuple[ColumnMapping, ...],
    ) -> float:
        """Weighted mean of the stage confidences, clamped to 0..1."""
        parts = {
            "document_classification": document_classification,
            "extraction_quality": extraction_quality,
            "header_confidence": header_confidence,
            "column_mapping": mapping_confidence(mappings),
        }
        total_weight = sum(self._weights.get(name, 0.0) for name in parts)
        if total_weight <= 0:
            return 0.0
        weighted = sum(value * self._weights.get(name, 0.0) for name, value in parts.items())
        return max(0.0, min(1.0, weighted / total_weight))

    def route(
        self,
        confidence: float,
        extraction_method: ExtractionMethod,
        coercion_failures: int = 0,
        surplus_unresolved: bool = False,
        identity_missing: bool = False,
    ) -> RoutingDecision:
        """Decide whether a row can be used unattended.

        Hard caps sit above the numeric score, because they describe failures a
        confidence value does not capture:

        OCR-derived rows never auto-accept however high they score. A misread digit in a
        money field is expensive and is not the kind of error a recognition confidence
        reliably predicts.

        Rows whose surplus column could not be determined never auto-accept either. Every
        other field may have resolved perfectly, but the one figure the business exists to
        find is unknown, and a high score would otherwise present that row as ready to work.

        Rows with no usable case identity never auto-accept. A wrap or footer line can
        inherit a table's mapping confidence while naming no case.
        """
        if confidence < self._review:
            return RoutingDecision.QUARANTINE

        if self._ocr_caps_at_review and extraction_method is ExtractionMethod.OCR:
            return RoutingDecision.REVIEW
        if surplus_unresolved:
            return RoutingDecision.REVIEW
        if identity_missing:
            return RoutingDecision.REVIEW

        return (
            RoutingDecision.AUTO_ACCEPT
            if confidence >= self._auto_accept and coercion_failures == 0
            else RoutingDecision.REVIEW
        )


def mapping_confidence(mappings: tuple[ColumnMapping, ...]) -> float:
    """Mean confidence across a table's columns.

    Unresolved columns contribute zero rather than being skipped: a table where half the
    columns could not be understood is genuinely less trustworthy than one where all of
    them were, and averaging only over the successes would hide exactly that.
    """
    if not mappings:
        return 0.0
    return sum(m.confidence for m in mappings) / len(mappings)
