from __future__ import annotations

import hashlib
from datetime import date

import structlog

from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.models import InterpretedRow
from surplus_ai.utils.hashing import stable_row_hash

logger = structlog.get_logger(__name__)

# Fields that identify a case, most specific first. A parcel identifies a property for as
# long as the county keeps its numbering; a case number identifies a proceeding. Either is
# a far better key than a name, which changes spelling between publications.
IDENTITY_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField.PARCEL_ID,
    CanonicalField.CASE_NUMBER,
    CanonicalField.CERTIFICATE_NUMBER,
    CanonicalField.UNIQUE_ID,
)


class CaseIdentity:
    """How one case is recognised again when its county republishes the list."""

    def __init__(
        self,
        dedupe_hash: str,
        basis: str,
        identifier: str | None,
        sale_date: date | None,
        is_positional: bool,
    ) -> None:
        self.dedupe_hash = dedupe_hash
        self.basis = basis
        """Which field the identity came from, or 'row_contents' for the fallback."""
        self.identifier = identifier
        self.sale_date = sale_date
        self.is_positional = is_positional
        """True when nothing identified the row and its full contents had to stand in."""


def case_identity(row: InterpretedRow) -> CaseIdentity:
    """Derive a stable identity for a case from what the county actually published.

    A published identifier is used when there is one, narrowed by the sale date where the
    county gives it -- the same parcel can go to sale in more than one year, and those are
    different cases over different money.

    When a county publishes no identifier at all, the row's verbatim contents stand in.
    That is a weaker key and it is recorded as such: it means a republication with a
    corrected spelling reads as a new case rather than the same one. The alternative --
    matching on owner name and amount -- would merge two neighbours who happen to be owed
    the same figure, and merging distinct people is the worse failure by a wide margin.
    """
    identifier, basis = _published_identifier(row)
    sale_date = _sale_date(row)

    if identifier is None:
        return CaseIdentity(
            dedupe_hash=stable_row_hash(row.raw_values),
            basis="row_contents",
            identifier=None,
            sale_date=sale_date,
            is_positional=True,
        )

    parts = [basis, identifier.strip().upper()]
    if sale_date is not None:
        parts.append(sale_date.isoformat())
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return CaseIdentity(
        dedupe_hash=digest,
        basis=basis,
        identifier=identifier.strip(),
        sale_date=sale_date,
        is_positional=False,
    )


def _published_identifier(row: InterpretedRow) -> tuple[str | None, str]:
    for field in IDENTITY_FIELDS:
        value = row.value(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text, field.value
    return None, "row_contents"


def _sale_date(row: InterpretedRow) -> date | None:
    value = row.value(CanonicalField.SALE_DATE)
    return value if isinstance(value, date) else None
