from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.classifier.models import ClassificationResult
from surplus_ai.classifier.owner_type_classifier import OwnerTypeClassifier
from surplus_ai.database.models.enums import ClassificationMethod, OwnerType
from surplus_ai.database.models.owner import Owner
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.models import InterpretedRow

logger = structlog.get_logger(__name__)


class OwnerBuildResult:
    """The owner recorded for a case, and what the classifier made of the name."""

    def __init__(self, owner: Owner | None, classification: ClassificationResult | None) -> None:
        self.owner = owner
        self.classification = classification

    @property
    def is_pursuable(self) -> bool:
        return self.classification is not None and self.classification.is_pursuable

    @property
    def owner_type(self) -> OwnerType:
        return self.classification.owner_type if self.classification else OwnerType.UNKNOWN


class OwnerBuilder:
    """Writes the owner recorded against a case, classified.

    One owner row per published owner cell, not one per human. Counties routinely publish
    "SMITH JOHN & MARY" in a single field; splitting that into two owner records would
    invent a party boundary the county never drew, and the halves would carry names nobody
    published. The classifier keeps the additional parties it can see alongside the split
    name, so nothing is lost by leaving the cell whole.

    A name that cannot be read is still written, as UNKNOWN at zero confidence. Storing it
    keeps the case complete; the pursuable check is what keeps it off a call list.
    """

    def __init__(self, session: Session, classifier: OwnerTypeClassifier | None = None) -> None:
        self._session = session
        self._classifier = classifier or OwnerTypeClassifier()

    def build(self, case: SurplusCase, row: InterpretedRow) -> OwnerBuildResult:
        raw_name = _owner_name(row)
        if raw_name is None:
            logger.debug("owner_absent", case_id=str(case.id))
            return OwnerBuildResult(None, None)

        classification = self._classifier.classify(raw_name)
        existing = self._session.scalar(
            select(Owner).where(Owner.surplus_case_id == case.id, Owner.raw_name == raw_name)
        )
        owner = existing or Owner(surplus_case_id=case.id, raw_name=raw_name)

        owner.owner_type = classification.owner_type
        owner.classification_confidence = classification.confidence
        owner.classification_method = ClassificationMethod(classification.method.value)

        person = classification.person_name
        owner.first_name = person.first if person else None
        owner.middle_name = person.middle if person else None
        owner.last_name = person.last if person else None
        owner.suffix = person.suffix if person else None
        is_person = classification.owner_type is OwnerType.INDIVIDUAL
        owner.entity_name = None if is_person else raw_name

        if existing is None:
            self._session.add(owner)
        self._session.flush()
        return OwnerBuildResult(owner, classification)


def _owner_name(row: InterpretedRow) -> str | None:
    """The owner as published.

    The purchaser is deliberately not a fallback. A purchaser is the party who bought at
    the sale; treating them as the owner would point the business at exactly the wrong
    person -- the one who took the property rather than the one owed the money.
    """
    value = row.value(CanonicalField.OWNER_NAME)
    if value is None:
        return None
    name = str(value).strip()
    return name or None
