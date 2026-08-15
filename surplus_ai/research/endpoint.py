"""Endpoint and Socrata identifier policy. Destinations come only from config."""

from __future__ import annotations

import ipaddress
import re
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
