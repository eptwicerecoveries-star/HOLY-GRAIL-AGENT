"""Generic cleanup when a typed identifier cell also holds trailing text.

Extraction can group a valid parcel token and the first word of an adjacent name column
into one cell. Interpretation peels them apart when the split is unambiguous, and fails
closed otherwise. Verbatim `raw_values` are never modified here.
"""

from __future__ import annotations

from dataclasses import dataclass

from surplus_ai.parser.interpretation.canonical import CanonicalField, FieldKind, kind_of
from surplus_ai.parser.interpretation.models import ColumnMapping
from surplus_ai.parser.interpretation.type_inference import (
    looks_like_date,
    looks_like_money,
    looks_like_parcel_id,
    looks_like_parcel_identity,
)

REVIEW_REASON_TYPED_CELL = "typed_cell_ambiguous"

_DESTINATION_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField.OWNER_NAME,
    CanonicalField.PURCHASER_NAME,
)
_TEXT_LIKE_TOKEN = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,'-&")


@dataclass(frozen=True)
class TypedCellCleanup:
    """Canonical updates from a typed-cell peel, or a fail-closed review reason."""

    updates: dict[CanonicalField, str]
    review_reasons: tuple[str, ...] = ()
    peeled: bool = False

    @property
    def ambiguous(self) -> bool:
        return REVIEW_REASON_TYPED_CELL in self.review_reasons


_NOOP = TypedCellCleanup(updates={})
_FAIL = TypedCellCleanup(updates={}, review_reasons=(REVIEW_REASON_TYPED_CELL,))


def apply_parcel_id_cell_cleanup(
    canonical: dict[CanonicalField, object],
    mappings: tuple[ColumnMapping, ...],
) -> TypedCellCleanup:
    """Peel trailing incompatible text off a Parcel ID cell when the split is safe.

    Operates on already-coerced canonical values. Callers keep `raw_values` untouched.
    """
    parcel = canonical.get(CanonicalField.PARCEL_ID)
    if parcel is None:
        return _NOOP
    text = str(parcel).strip()
    if not text:
        return _NOOP

    tokens = text.split()
    if len(tokens) < 2:
        return _NOOP

    parcel_indexes = [
        index
        for index, mapping in enumerate(mappings)
        if mapping.canonical_field is CanonicalField.PARCEL_ID
    ]
    if not parcel_indexes:
        return _NOOP
    if len(parcel_indexes) != 1:
        return _FAIL

    cut = _first_text_like_index(tokens)
    if cut is None:
        return _numeric_remainder_verdict(tokens)

    if cut == 0:
        return _FAIL

    prefix_tokens = tokens[:cut]
    prefix = " ".join(prefix_tokens)
    suffix = " ".join(tokens[cut:])
    if not prefix or prefix_tokens != prefix.split():
        return _FAIL
    if not looks_like_parcel_identity(prefix):
        return _FAIL
    if not _suffix_is_safe_text(suffix):
        return _FAIL

    destination = _resolve_destination(mappings, parcel_indexes[0])
    if destination is None:
        return _FAIL
    dest_field, side = destination
    if kind_of(dest_field) not in {FieldKind.PERSON, FieldKind.TEXT}:
        return _FAIL
    if dest_field not in _DESTINATION_FIELDS:
        return _FAIL

    current = canonical.get(dest_field)
    current_text = "" if current is None else str(current).strip()
    joined = _join(suffix, current_text, side)
    return TypedCellCleanup(
        updates={CanonicalField.PARCEL_ID: prefix, dest_field: joined},
        peeled=True,
    )


def _first_text_like_index(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens):
        if _is_text_like_token(token):
            return index
    return None


def _numeric_remainder_verdict(tokens: list[str]) -> TypedCellCleanup:
    """No text-like token: keep spaced identifiers, review numeric leftovers after a prefix."""
    first, remainder = tokens[0], " ".join(tokens[1:])
    if not remainder:
        return _NOOP
    if looks_like_parcel_identity(first) and remainder:
        return _FAIL
    return _NOOP


def _suffix_is_safe_text(suffix: str) -> bool:
    text = suffix.strip()
    if not text:
        return False
    if looks_like_date(text) or looks_like_money(text):
        return False
    if looks_like_parcel_identity(text) or looks_like_parcel_id(text):
        return False
    if any(character.isdigit() for character in text):
        return False
    tokens = text.split()
    if not tokens or not all(_is_text_like_token(token) for token in tokens):
        return False
    return any(character.isalpha() for character in text)


def _is_text_like_token(token: str) -> bool:
    if not token or any(character.isdigit() for character in token):
        return False
    if not any(character.isalpha() for character in token):
        return False
    return all(character in _TEXT_LIKE_TOKEN for character in token)


def _resolve_destination(
    mappings: tuple[ColumnMapping, ...], parcel_index: int
) -> tuple[CanonicalField, str] | None:
    """Pick a single adjacent person field. Owner wins over purchaser; both sides fail closed."""
    left = _destination_at(mappings, parcel_index - 1)
    right = _destination_at(mappings, parcel_index + 1)
    if left is not None and right is not None:
        if left is CanonicalField.OWNER_NAME and right is not CanonicalField.OWNER_NAME:
            return left, "left"
        if right is CanonicalField.OWNER_NAME and left is not CanonicalField.OWNER_NAME:
            return right, "right"
        return None
    if right is CanonicalField.OWNER_NAME:
        return right, "right"
    if left is CanonicalField.OWNER_NAME:
        return left, "left"
    if right is CanonicalField.PURCHASER_NAME:
        return right, "right"
    if left is CanonicalField.PURCHASER_NAME:
        return left, "left"
    return None


def _destination_at(
    mappings: tuple[ColumnMapping, ...], index: int
) -> CanonicalField | None:
    if index < 0 or index >= len(mappings):
        return None
    field = mappings[index].canonical_field
    if field in _DESTINATION_FIELDS:
        return field
    return None


def _join(suffix: str, existing: str, side: str) -> str:
    suffix = suffix.strip()
    existing = existing.strip()
    if not existing:
        return suffix
    if not suffix:
        return existing
    if side == "right":
        return f"{suffix} {existing}"
    return f"{existing} {suffix}"
