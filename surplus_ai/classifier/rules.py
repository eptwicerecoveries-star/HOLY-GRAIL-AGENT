from __future__ import annotations

import re

from surplus_ai.classifier.entity_keywords import (
    EntityKeywordSet,
    matches_whole_word,
    normalize_name,
)
from surplus_ai.classifier.models import PersonName
from surplus_ai.database.models.enums import OwnerType

# Owner names in county lists are rarely long. A very long string is usually two records
# run together or a mis-split cell, and is better reported as unknown than guessed at.
MAX_REASONABLE_NAME_LENGTH = 120

# Recognition leaves fragments like "&" or "| &" behind where a cell held only a
# continuation mark. These are not names and must not become leads.
_NOISE_RE = re.compile(r"^[\W_]*$")
_PARTY_SPLIT_RE = re.compile(r"\s+(?:&|and)\s+", re.IGNORECASE)


class RuleBasedClassifier:
    """Decides an owner's kind from vocabulary, in a fixed order of authority.

    Order matters and is not arbitrary. Estate is tested before company because
    "ESTATE OF LOIS BYRD" contains neither a company marker nor a person's ordinary shape,
    and because an estate is a lead rather than something to discard. Government is tested
    before company because a county's own name would otherwise read as a business.
    """

    def __init__(self, keywords: EntityKeywordSet) -> None:
        self._keywords = keywords

    def classify_type(self, raw_name: str) -> tuple[OwnerType, tuple[str, ...]]:
        """Return the owner kind and whichever markers decided it."""
        normalized = normalize_name(raw_name)
        if not normalized or _NOISE_RE.match(raw_name.strip()):
            return OwnerType.UNKNOWN, ()

        for owner_type in (
            OwnerType.GOVERNMENT,
            OwnerType.ESTATE,
            OwnerType.TRUST,
            OwnerType.COMPANY,
        ):
            matched = self._matched(normalized, owner_type)
            if matched:
                return owner_type, matched

        if looks_like_person(normalized, self._keywords):
            return OwnerType.INDIVIDUAL, ()
        return OwnerType.UNKNOWN, ()

    def _matched(self, normalized: str, owner_type: OwnerType) -> tuple[str, ...]:
        return tuple(
            marker
            for marker in self._keywords.markers_for(owner_type)
            if matches_whole_word(normalized, marker)
        )


def looks_like_person(normalized: str, keywords: EntityKeywordSet) -> bool:
    """Whether a name has the shape of a person rather than an organisation."""
    if len(normalized) > MAX_REASONABLE_NAME_LENGTH:
        return False
    words = [w for w in normalized.split() if w]
    if not words:
        return False
    if not any(any(c.isalpha() for c in word) for word in words):
        return False

    meaningful = [w for w in words if w not in keywords.person_suffixes and w != "&"]
    # A single token is a surname at best and is not enough to act on; two or more is the
    # ordinary shape of a person, in either the "SMITH JOHN" or "Smith, John" order.
    return len(meaningful) >= 2


def split_person_name(raw_name: str, keywords: EntityKeywordSet) -> PersonName:
    """Split a person's name into parts, tolerating both orders counties use.

    "STEFFEN, DEBORAH ANDERS" is surname-first with a comma; "GIBSON STEWART D" is
    surname-first without one. Both appear in the corpus, sometimes in the same file, so
    the comma is treated as the only reliable signal and its absence is read as
    surname-first by convention.

    Joint owners ("WHYTE, WALTER & CATHERINE") keep the first party as the name and the
    rest as additional parties, because the money is owed to all of them and dropping the
    others would lose a claimant.
    """
    cleaned = " ".join(raw_name.split())
    parties = _PARTY_SPLIT_RE.split(cleaned)
    primary = parties[0].strip()
    additional = tuple(p.strip() for p in parties[1:] if p.strip())

    suffix: str | None = None
    if "," in primary:
        surname, _, remainder = primary.partition(",")
        tokens = [t for t in remainder.split() if t]
    else:
        tokens = [t for t in primary.split() if t]
        surname = tokens[0] if tokens else ""
        tokens = tokens[1:]

    if tokens and normalize_name(tokens[-1]) in keywords.person_suffixes:
        suffix = tokens[-1]
        tokens = tokens[:-1]

    first = tokens[0] if tokens else None
    middle = " ".join(tokens[1:]) if len(tokens) > 1 else None

    return PersonName(
        raw=raw_name,
        first=first,
        middle=middle,
        last=surname.strip() or None,
        suffix=suffix,
        additional_parties=additional,
    )
