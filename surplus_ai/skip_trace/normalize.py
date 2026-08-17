"""Narrow deterministic phone/email normalization for Contact materialization V1."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def normalize_phone(raw: str) -> str | None:
    """US-centric V1: 10 digits or 11-digit NANP starting with 1 → ``+1XXXXXXXXXX``.

    Rejects empty/whitespace, non-digit-heavy values, and non-NANP lengths.
    Does not invent country codes or rewrite questionable numbers.
    """
    text = (raw or "").strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def normalize_email(raw: str) -> str | None:
    """Trim + lowercase; reject clearly malformed. No domain guessing or alias folding."""
    text = (raw or "").strip().lower()
    if not text or " " in text or text.count("@") != 1:
        return None
    if not _EMAIL_RE.match(text):
        return None
    return text
