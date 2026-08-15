"""Endpoint and Socrata identifier policy. Destinations come only from config."""

from __future__ import annotations

import ipaddress
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from surplus_ai.research.dns import destination_is_unsafe
from surplus_ai.research.exceptions import ResearchConfigError

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)
_DATASET_ID = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
_FIELD_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SOCRATA_NUMBER_IDENTITY = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_SYSTEM_ID = ":id"
_BLOCKED_SCHEMES = frozenset({"http", "file", "ftp", "data", "javascript", "ws", "wss"})


def validate_socrata_domain(domain: str) -> str:
    """Return a lowercase DNS hostname. Reject schemes, IPs, and local/private hosts."""
    value = domain.strip().lower().rstrip(".")
    if not value:
        raise ResearchConfigError("Socrata domain must be a non-empty hostname")
    if "://" in value or "/" in value or "\\" in value or "@" in value or ":" in value:
        raise ResearchConfigError("Socrata domain must be a hostname without a scheme or path")
    _reject_blocked_host(value)
    if not _HOSTNAME.match(value):
        raise ResearchConfigError(f"Invalid Socrata domain {domain!r}")
    return value


def _reject_blocked_host(host: str) -> None:
    lowered = host.strip().lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise ResearchConfigError("Host must not be localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    _reject_blocked_ip(address)
    # Socrata domains are DNS names. IP literals are rejected even if public.
    raise ResearchConfigError("Host must be a DNS hostname, not an IP address")


def _reject_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if destination_is_unsafe(address):
        raise ResearchConfigError(
            "Host must not be a loopback, private, link-local, or unspecified address"
        )


def validate_socrata_dataset_id(dataset_id: str) -> str:
    value = dataset_id.strip().lower()
    if not _DATASET_ID.match(value):
        raise ResearchConfigError(
            "Socrata dataset_id must be four alphanumeric characters, a hyphen, "
            "and four more (for example abcd-1234)"
        )
    return value


def validate_socrata_field_id(name: str, *, allow_system_id: bool = False) -> str:
    value = name.strip()
    if allow_system_id and value == _SYSTEM_ID:
        return value
    if not _FIELD_ID.match(value):
        raise ResearchConfigError(
            f"Invalid Socrata field identifier {name!r}; "
            "use letters, digits, and underscores only"
        )
    return value


def socrata_resource_url(domain: str, dataset_id: str) -> str:
    """Canonical dataset JSON endpoint. Never includes a query string."""
    host = validate_socrata_domain(domain)
    resource = validate_socrata_dataset_id(dataset_id)
    return f"https://{host}/resource/{resource}.json"


def validate_public_https_url(url: str) -> str:
    """Accept only https URLs to public DNS hosts. Does not follow redirects or resolve DNS."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme != "https":
        raise ResearchConfigError("Only https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise ResearchConfigError("URLs must not contain userinfo")
    host = parts.hostname
    if not host:
        raise ResearchConfigError("URL must include a hostname")
    _reject_blocked_host(host)
    if not _HOSTNAME.match(host):
        raise ResearchConfigError("URL hostname is not a valid DNS name")
    return url


def soql_string_literal(value: str) -> str:
    """Escape a SoQL string value. Identifiers must already have been validated."""
    return "'" + value.replace("'", "''") + "'"


def parse_socrata_number_identity(value: object) -> Decimal | None:
    """Parse a conservative non-negative Socrata Number identity.

    Accepts digit strings, JSON integers, and ``Decimal`` values from JSON
    ``parse_float``. Rejects signs, exponents, hex, underscores, whitespace,
    booleans, and non-finite values. Does not convert through ``float``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value < 0:
            return None
        return Decimal(value)
    if isinstance(value, Decimal):
        if value.is_nan() or not value.is_finite() or value < 0:
            return None
        return value
    if isinstance(value, str):
        if not _SOCRATA_NUMBER_IDENTITY.fullmatch(value):
            return None
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return None
        if parsed.is_nan() or not parsed.is_finite() or parsed < 0:
            return None
        return parsed
    return None


def canonical_socrata_number_literal(value: Decimal) -> str:
    """Fixed-point SoQL number literal. Never uses float or exponent notation.

    Leading zeros that are numerically irrelevant are dropped by Decimal
    construction. Trailing zeros are stripped only from the fractional part,
    so integer-place zeros in values such as ``1000`` and ``1000160100``
    stay significant. Canonical zero is ``0``. Formatting does not call
    ``Decimal.normalize()``, which applies the active context precision.
    """
    text = format(value, "f")
    if "." in text:
        integer, fraction = text.split(".", 1)
        fraction = fraction.rstrip("0")
        text = integer if not fraction else f"{integer}.{fraction}"
    return text or "0"


def soql_number_literal(value: str) -> str:
    """Unquoted SoQL number literal after strict lexical validation."""
    parsed = parse_socrata_number_identity(value)
    if parsed is None:
        raise ValueError("invalid socrata number identity")
    return canonical_socrata_number_literal(parsed)
