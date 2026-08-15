"""Resolved-address policy for research HTTPS. Resolver input is hostname + port only.

Destination rule (Python 3.12 ``ipaddress``):

1. Unwrap IPv4-mapped IPv6 (``::ffff:x.x.x.x``) and apply the IPv4 rule.
2. Reject every Teredo address (``2001:0::/32`` / ``IPv6Address.teredo``).
3. Reject the RFC 8215 NAT64 prefix ``64:ff9b:1::/48``.
4. For well-known NAT64 ``64:ff9b::/96``, apply the IPv4 rule to the embedded address.
5. For 6to4 (``IPv6Address.sixtofour``), reject when the embedded IPv4 is unsafe.
6. Reject if ``(not addr.is_global) or addr.is_multicast``.

``is_global`` alone is not sufficient: Python 3.12 reports some multicast
addresses as global. ``is_private`` alone misses CGNAT ``100.64.0.0/10``.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence
from typing import Protocol

MAX_RESOLVED_ADDRESSES = 16

_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")
_TEREDO = ipaddress.IPv6Network("2001:0::/32")

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class DnsResolutionError(Exception):
    """Resolver failed or returned no addresses. Safe to retry. Never persist str(self)."""

    def __str__(self) -> str:
        return "dns_resolution_failed"


class UnsafeResolvedAddressError(Exception):
    """Resolution set is unsafe or oversized. Do not connect. Never persist str(self)."""

    def __str__(self) -> str:
        return "unsafe_resolved_address"


class Resolver(Protocol):
    def resolve(self, hostname: str, port: int) -> Sequence[IpAddress]: ...


class SystemResolver:
    """``socket.getaddrinfo`` with SOCK_STREAM / TCP. Hostname and port only."""

    def resolve(self, hostname: str, port: int) -> Sequence[IpAddress]:
        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise DnsResolutionError() from exc
        addresses: list[IpAddress] = []
        for record in records:
            sockaddr = record[4]
            if not sockaddr:
                raise UnsafeResolvedAddressError()
            raw_host = sockaddr[0]
            if not isinstance(raw_host, str):
                raise UnsafeResolvedAddressError()
            addresses.append(_parse_resolved_host(raw_host))
        return _dedupe_preserve_order(addresses)


def _parse_resolved_host(raw_host: str) -> IpAddress:
    host = raw_host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(host)
    except ValueError as exc:
        raise UnsafeResolvedAddressError() from exc


def _dedupe_preserve_order(addresses: Sequence[IpAddress]) -> tuple[IpAddress, ...]:
    seen: set[str] = set()
    unique: list[IpAddress] = []
    for address in addresses:
        key = address.compressed
        if key in seen:
            continue
        seen.add(key)
        unique.append(address)
    return tuple(unique)


def destination_is_unsafe(address: IpAddress) -> bool:
    """True when *address* must not be used as a research TCP peer."""
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return destination_is_unsafe(mapped)
        if address.teredo is not None or address in _TEREDO:
            return True
        if address in _NAT64_LOCAL:
            return True
        if address in _NAT64_WELL_KNOWN:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            return destination_is_unsafe(embedded)
        sixtofour = address.sixtofour
        if sixtofour is not None and destination_is_unsafe(sixtofour):
            return True
    return (not address.is_global) or address.is_multicast


def validate_resolved_addresses(addresses: Sequence[IpAddress]) -> tuple[IpAddress, ...]:
    """Return unique addresses in resolver order, or raise a structured DNS error.

    Fail closed if any address is unsafe. Never returns a public subset of a mixed set.
    """
    if not addresses:
        raise DnsResolutionError()
    unique = _dedupe_preserve_order(addresses)
    if len(unique) > MAX_RESOLVED_ADDRESSES:
        raise UnsafeResolvedAddressError()
    if any(destination_is_unsafe(address) for address in unique):
        raise UnsafeResolvedAddressError()
    return unique


def connect_host(address: IpAddress) -> str:
    """Numeric form passed to the inner TCP connector. Never a DNS hostname."""
    return address.compressed
