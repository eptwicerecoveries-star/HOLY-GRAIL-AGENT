from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateutil_parser

from surplus_ai.parser.interpretation.canonical import CanonicalField, FieldKind

# Currency with optional symbol, thousands separators and accounting-style negatives.
CURRENCY_RE = re.compile(r"^\(?\s*-?\s*\$?\s*-?[\d,]+(?:\.\d{1,2})?\s*\)?$")
DATE_RE = re.compile(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
PARCEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-./ ]{2,}$")
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
ENTITY_MARKERS = (
    " llc",
    " inc",
    " corp",
    " company",
    " co ",
    " ltd",
    " lp",
    " llp",
    " trust",
    " properties",
    " holdings",
    " partners",
    " bank",
    " association",
)

# Trailing timezone abbreviations confuse date parsing and carry no information we use.
_TRAILING_TZ_RE = re.compile(r"\s+(?:[A-Z]{2,4}T|UTC|GMT)$")


def parse_money(value: str) -> Decimal | None:
    """Parse a published money string, or return None if it is not one.

    Returns None rather than zero for unparseable input. Zero is a real amount in this
    domain -- a fully refunded overbid is genuinely $0.00 -- so it must never double as
    "no value".
    """
    text = value.strip()
    if not text or not CURRENCY_RE.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -amount if negative and amount > 0 else amount


def parse_date(value: str) -> date | None:
    """Parse a published date, tolerating an appended time and timezone."""
    text = _TRAILING_TZ_RE.sub("", value.strip())
    if not text:
        return None
    match = DATE_RE.search(text)
    candidate = match.group(0) if match else text
    try:
        return dateutil_parser.parse(candidate, fuzzy=False).date()
    except (ValueError, OverflowError, TypeError):
        return None


def looks_like_money(value: str) -> bool:
    return parse_money(value) is not None


def looks_like_date(value: str) -> bool:
    return parse_date(value) is not None


def looks_like_year(value: str) -> bool:
    return bool(YEAR_RE.match(value.strip()))


def has_usable_case_identity(canonical: dict[CanonicalField, object]) -> bool:
    """True when a row itself carries an identifier a person could use to find the case.

    Table-level mapping confidence is not enough: a wrap line can sit under a perfectly
    mapped header row and still name nobody. Alphabetic leftovers in a parcel column
    do not count; `looks_like_parcel_id` requires a digit.
    """
    for field in (
        CanonicalField.CASE_NUMBER,
        CanonicalField.CERTIFICATE_NUMBER,
        CanonicalField.UNIQUE_ID,
    ):
        value = canonical.get(field)
        if value is not None and str(value).strip():
            return True
    parcel = canonical.get(CanonicalField.PARCEL_ID)
    return parcel is not None and looks_like_parcel_identity(str(parcel))


def looks_like_parcel_id(value: str) -> bool:
    """Identifier-shaped: alphanumeric with separators, and not purely alphabetic."""
    text = value.strip()
    if not text or looks_like_money(text) or looks_like_date(text):
        return False
    if not PARCEL_RE.match(text):
        return False
    return any(c.isdigit() for c in text)


def looks_like_parcel_identity(value: str) -> bool:
    """Whether a value can identify a parcel, including compact digit STRAPs.

    `looks_like_parcel_id` rejects long digit strings because they also match the money
    pattern. A case identity check must still accept those published parcel numbers.
    """
    if looks_like_parcel_id(value):
        return True
    text = value.strip()
    if len(text) < 6 or looks_like_date(text):
        return False
    if not any(c.isdigit() for c in text):
        return False
    if "$" in text or "." in text or "(" in text:
        return False
    return bool(PARCEL_RE.match(text))


def looks_like_person_name(value: str) -> bool:
    """Person-shaped rather than entity-shaped.

    Entity detection here is only strong enough to keep the value-inference fallback
    honest. Real owner-type classification is a later phase and is not attempted.
    """
    text = value.strip()
    if len(text) < 3 or looks_like_money(text) or looks_like_date(text):
        return False
    if not any(c.isalpha() for c in text):
        return False
    padded = f" {text.casefold()} "
    if any(marker in padded for marker in ENTITY_MARKERS):
        return False
    return len(text.split()) >= 2 or "," in text


def looks_like_address(value: str) -> bool:
    """Street-address shaped: has a number and enough words to be more than a name."""
    text = value.strip()
    if len(text) < 6 or looks_like_money(text):
        return False
    has_digit = any(c.isdigit() for c in text)
    return has_digit and (len(text.split()) >= 3 or bool(ZIP_RE.search(text)))


def infer_field_kind(values: list[str]) -> FieldKind | None:
    """Infer what a column holds from a sample of its values.

    Used only when a header is missing or unrecognised. The column is still preserved
    either way; this just decides whether a canonical field can be proposed for it.
    """
    populated = [v for v in values if v.strip()]
    if not populated:
        return None

    total = len(populated)
    checks: list[tuple[FieldKind, float]] = [
        (FieldKind.MONEY, _share(populated, looks_like_money)),
        (FieldKind.DATE, _share(populated, looks_like_date)),
        (FieldKind.ADDRESS, _share(populated, looks_like_address)),
        (FieldKind.IDENTIFIER, _share(populated, looks_like_parcel_id)),
        (FieldKind.PERSON, _share(populated, looks_like_person_name)),
    ]
    kind, share = max(checks, key=lambda item: item[1])
    if share < 0.7 or total < 3:
        return None
    return kind


def coerce(field: CanonicalField, value: str, kind: FieldKind) -> Decimal | date | str | None:
    """Convert a verbatim value to its typed form.

    The verbatim string is always retained alongside this result; a failure here lowers
    confidence and is recorded, but never removes the value or the row.
    """
    text = value.strip()
    if not text:
        return None
    if kind is FieldKind.MONEY:
        return parse_money(text)
    if kind is FieldKind.DATE:
        return parse_date(text)
    return text


def _share(values: list[str], predicate: Callable[[str], bool]) -> float:
    matches = sum(1 for v in values if predicate(v))
    return matches / len(values)
