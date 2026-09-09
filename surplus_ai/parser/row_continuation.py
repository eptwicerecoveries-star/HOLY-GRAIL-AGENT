from __future__ import annotations

from dataclasses import dataclass

import structlog

from surplus_ai.parser.headers import DEFAULT_MIN_HEADER_SCORE
from surplus_ai.parser.interpretation.alias_registry import AliasRegistry, load_alias_registry
from surplus_ai.parser.interpretation.canonical import (
    MONEY_FIELDS,
    CanonicalField,
    FieldKind,
    kind_of,
)
from surplus_ai.parser.interpretation.type_inference import (
    looks_like_date,
    looks_like_money,
    looks_like_parcel_identity,
)
from surplus_ai.parser.quality import row_header_score
from surplus_ai.parser.stitching import row_is_data_like
from surplus_ai.parser.strategies.base import normalize_cell

logger = structlog.get_logger(__name__)

IDENTITY_FIELDS: frozenset[CanonicalField] = frozenset(
    {
        CanonicalField.CASE_NUMBER,
        CanonicalField.CERTIFICATE_NUMBER,
        CanonicalField.UNIQUE_ID,
    }
)
PROTECTED_TYPED_FIELDS: frozenset[CanonicalField] = (
    IDENTITY_FIELDS
    | MONEY_FIELDS
    | frozenset(
        {
            CanonicalField.PARCEL_ID,
            CanonicalField.SALE_DATE,
            CanonicalField.FORECLOSURE_DATE,
            CanonicalField.REDEMPTION_DEADLINE,
            CanonicalField.CLAIM_DEADLINE,
        }
    )
)
_NON_RECORD_LABELS = frozenset(
    {
        "total",
        "subtotal",
        "grand total",
        "totals",
        "continued",
        "continued on next page",
        "balance forward",
        "page",
    }
)


@dataclass(frozen=True)
class FoldedPhysicalRow:
    """A kept physical row after continuation lines have been folded into it."""

    page_number: int
    row_index_on_page: int
    cells: tuple[str, ...]
    continuation_sources: tuple[tuple[int, int], ...] = ()

    @property
    def continuation_count(self) -> int:
        return len(self.continuation_sources)


def fold_stitched_rows(
    headers: tuple[str, ...],
    rows: list[tuple[int, int, tuple[str, ...]]],
    registry: AliasRegistry | None = None,
) -> list[FoldedPhysicalRow]:
    """Fold headerless wrap lines into the preceding primary row.

    Operates on a stitched logical table, so a wrap that starts on the last line of one
    page and finishes on the next is the same problem as a wrap in the middle of a page.
    Ambiguous typed leftovers fail closed: the physical row is kept for review rather
    than guessed into a neighbouring field.
    """
    alias = registry or load_alias_registry()
    fields = tuple(alias.match_exact(header) for header in headers)
    owner_index = _owner_index(fields)
    kept: list[FoldedPhysicalRow] = []
    folded = 0
    leftover = 0

    for page_number, row_index, cells in rows:
        if not any(cell.strip() for cell in cells):
            continue
        cells = tuple(normalize_cell(cell) if cell else "" for cell in cells)
        target = kept[-1] if kept else None
        if (
            target is not None
            and owner_index is not None
            and _is_primary(target.cells, fields)
            and _is_continuation_candidate(cells, fields)
        ):
            merged = _append_continuation(target.cells, cells, fields, owner_index)
            if merged is not None:
                sources = (*target.continuation_sources, (page_number, row_index))
                kept[-1] = FoldedPhysicalRow(
                    page_number=target.page_number,
                    row_index_on_page=target.row_index_on_page,
                    cells=merged,
                    continuation_sources=sources,
                )
                folded += 1
                continue
            leftover += 1
        kept.append(
            FoldedPhysicalRow(
                page_number=page_number,
                row_index_on_page=row_index,
                cells=cells,
            )
        )

    if folded or leftover:
        logger.debug(
            "continuation_rows_folded",
            folded=folded,
            leftover=leftover,
            kept=len(kept),
        )
    return kept


def _owner_index(fields: tuple[CanonicalField | None, ...]) -> int | None:
    try:
        return fields.index(CanonicalField.OWNER_NAME)
    except ValueError:
        return None


def _is_primary(cells: tuple[str, ...], fields: tuple[CanonicalField | None, ...]) -> bool:
    """True when the row carries a generic case identity, not a county-specific label."""
    has_identity = False
    parcel_ok = False
    has_date = False
    has_money = False
    for field, value in zip(fields, cells, strict=False):
        text = value.strip()
        if not text or field is None:
            continue
        if field in IDENTITY_FIELDS:
            has_identity = True
        if field is CanonicalField.PARCEL_ID and looks_like_parcel_identity(text):
            parcel_ok = True
        if kind_of(field) is FieldKind.DATE and looks_like_date(text):
            has_date = True
        if field in MONEY_FIELDS and looks_like_money(text):
            has_money = True
    if has_identity:
        return True
    return parcel_ok and (has_date or has_money)


def _is_continuation_candidate(
    cells: tuple[str, ...], fields: tuple[CanonicalField | None, ...]
) -> bool:
    if _looks_like_header(cells) or _looks_like_non_record(cells):
        return False
    if not any(cell.strip() for cell in cells):
        return False
    for field, value in zip(fields, cells, strict=False):
        text = value.strip()
        if not text:
            continue
        if looks_like_date(text) or looks_like_money(text):
            return False
        if field in IDENTITY_FIELDS:
            return False
        if field is CanonicalField.PARCEL_ID and looks_like_parcel_identity(text):
            return False
    return True


def _append_continuation(
    target: tuple[str, ...],
    continuation: tuple[str, ...],
    fields: tuple[CanonicalField | None, ...],
    owner_index: int,
) -> tuple[str, ...] | None:
    """Join wrap text into the owner cell. Never write into typed identity/money/date fields."""
    merged = list(target)
    width = min(len(continuation), len(fields), len(merged))
    for index in range(width):
        text = continuation[index].strip()
        if not text:
            continue
        field = fields[index]
        if field is CanonicalField.OWNER_NAME:
            merged[owner_index] = _join_text(merged[owner_index], text)
            continue
        if field is CanonicalField.PROPERTY_ADDRESS:
            return None
        if field in PROTECTED_TYPED_FIELDS:
            # Token cannot validly belong in the typed column (candidate already rejected
            # parcel-shaped / date / money values). Route leftover letters to owner.
            if field is CanonicalField.PARCEL_ID and looks_like_parcel_identity(text):
                return None
            merged[owner_index] = _join_text(merged[owner_index], text)
            continue
        merged[owner_index] = _join_text(merged[owner_index], text)
    for extra in continuation[width:]:
        if extra.strip():
            merged[owner_index] = _join_text(merged[owner_index], extra)
    return tuple(merged)


def _join_text(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}"


def _looks_like_header(cells: tuple[str, ...]) -> bool:
    filled = [cell for cell in cells if cell.strip()]
    if len(filled) < max(2, len(cells) // 2):
        return False
    if row_is_data_like(cells):
        return False
    return row_header_score(cells) >= DEFAULT_MIN_HEADER_SCORE


def _looks_like_non_record(cells: tuple[str, ...]) -> bool:
    values = [cell.strip().casefold() for cell in cells if cell.strip()]
    return bool(values) and all(value in _NON_RECORD_LABELS for value in values)
