from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence

import httpcore
import pytest

from surplus_ai.research.dns import (
    MAX_RESOLVED_ADDRESSES,
    DnsResolutionError,
    SystemResolver,
    UnsafeResolvedAddressError,
    connect_host,
    destination_is_unsafe,
    validate_resolved_addresses,
)
from surplus_ai.research.http import ValidatingNetworkBackend


class FakeResolver:
    def __init__(
        self,
        addresses: Sequence[ipaddress.IPv4Address | ipaddress.IPv6Address] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.addresses = list(addresses or [])
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def resolve(
        self, hostname: str, port: int
    ) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        self.calls.append((hostname, port))
        if self.error is not None:
            raise self.error
        return list(self.addresses)


class RecordingBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        *,
        stream: httpcore.NetworkStream | None = None,
        errors: list[BaseException] | None = None,
        clock: ManualClock | None = None,
        advance: float = 0.0,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.unix_paths: list[str] = []
        self._stream = stream or httpcore.MockStream([b""])
        self._errors = list(errors or [])
        self._clock = clock
        self._advance = advance

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.NetworkStream:
        self.calls.append({"host": host, "port": port, "timeout": timeout})
        if self._clock is not None and self._advance:
            self._clock.t += self._advance
        if self._errors:
            raise self._errors.pop(0)
        return self._stream

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: object = None,
    ) -> httpcore.NetworkStream:
        self.unix_paths.append(path)
        raise AssertionError("UDS must not be used")


class ManualClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _v4(value: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(value)


def _v6(value: str) -> ipaddress.IPv6Address:
    return ipaddress.IPv6Address(value)


def _nat64(ipv4: str) -> ipaddress.IPv6Address:
    embedded = ipaddress.IPv4Address(ipv4)
    return ipaddress.IPv6Address(int(ipaddress.IPv6Address("64:ff9b::")) + int(embedded))


def _sixtofour(ipv4: str) -> ipaddress.IPv6Address:
    packed = ipaddress.IPv4Address(ipv4).packed
    return ipaddress.IPv6Address(bytes.fromhex("2002") + packed + bytes(10))


@pytest.mark.parametrize(
    "value",
    ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"],
)
def test_public_destinations_accepted(value: str) -> None:
    address = ipaddress.ip_address(value)
    assert destination_is_unsafe(address) is False
    assert validate_resolved_addresses([address]) == (address,)


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.1",
        "172.16.4.4",
        "192.168.1.8",
        "fc00::1",
        "fd12:3456:789a::1",
        "169.254.1.1",
        "fe80::1",
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "ff02::1",
        "255.255.255.255",
        "203.0.113.1",
        "192.0.2.1",
        "2001:db8::1",
        "100.64.0.1",
        "100.127.255.254",
        "::ffff:127.0.0.1",
        "::ffff:10.1.2.3",
        "::ffff:192.168.0.9",
        "::ffff:100.64.0.1",
        "::ffff:169.254.1.1",
        "::ffff:224.0.0.1",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
        "64:ff9b:1::1",
    ],
)
def test_unsafe_destinations_rejected(value: str) -> None:
    address = ipaddress.ip_address(value)
    assert destination_is_unsafe(address) is True
    with pytest.raises(UnsafeResolvedAddressError):
        validate_resolved_addresses([address])


def test_unsafe_6to4_embedded_private() -> None:
    address = _sixtofour("10.0.0.1")
    assert address.sixtofour == _v4("10.0.0.1")
    assert destination_is_unsafe(address) is True


def test_unsafe_nat64_embedded_private() -> None:
    address = _nat64("192.168.1.1")
    assert destination_is_unsafe(address) is True


def test_nat64_with_public_embedded_ipv4_accepted() -> None:
    address = _nat64("8.8.8.8")
    assert destination_is_unsafe(address) is False


def test_mixed_public_and_private_fails_closed() -> None:
    with pytest.raises(UnsafeResolvedAddressError):
        validate_resolved_addresses([_v4("8.8.8.8"), _v4("10.0.0.1")])


def test_empty_resolution_is_dns_failure() -> None:
    with pytest.raises(DnsResolutionError):
        validate_resolved_addresses([])


def test_duplicate_ips_deduped_preserving_order() -> None:
    unique = validate_resolved_addresses(
        [_v4("8.8.8.8"), _v4("1.1.1.1"), _v4("8.8.8.8"), _v6("2001:4860:4860::8888")]
    )
    assert unique == (_v4("8.8.8.8"), _v4("1.1.1.1"), _v6("2001:4860:4860::8888"))


def test_oversized_resolution_set_rejected() -> None:
    addresses = [_v4(f"1.1.1.{i}") for i in range(1, MAX_RESOLVED_ADDRESSES + 2)]
    with pytest.raises(UnsafeResolvedAddressError):
        validate_resolved_addresses(addresses)


def test_system_resolver_calls_getaddrinfo_with_hostname_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, ...]] = []

    def fake_getaddrinfo(
        host: str,
        port: int,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[object, ...]]:
        seen.append((host, port, type, proto))
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", port),
            )
        ]

    monkeypatch.setattr("surplus_ai.research.dns.socket.getaddrinfo", fake_getaddrinfo)
    addresses = SystemResolver().resolve("opendata.example.gov", 443)
    assert seen == [
        ("opendata.example.gov", 443, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    ]
    assert addresses == (_v4("8.8.8.8"),)


def test_system_resolver_oserror_is_dns_resolution_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise socket.gaierror(socket.EAI_NONAME, "secret.example query=parcel token=abc")

    monkeypatch.setattr("surplus_ai.research.dns.socket.getaddrinfo", boom)
    with pytest.raises(DnsResolutionError) as exc_info:
        SystemResolver().resolve("opendata.example.gov", 443)
    assert str(exc_info.value) == "dns_resolution_failed"
    assert "secret.example" not in str(exc_info.value)
    assert "parcel" not in str(exc_info.value)
    assert "token" not in str(exc_info.value)


def test_connect_tcp_pins_validated_numeric_ip() -> None:
    resolver = FakeResolver([_v4("8.8.8.8")])
    inner = RecordingBackend()
    backend = ValidatingNetworkBackend(resolver=resolver, inner=inner)
    backend.connect_tcp("opendata.example.gov", 443, timeout=5.0)
    assert resolver.calls == [("opendata.example.gov", 443)]
    assert inner.calls == [{"host": "8.8.8.8", "port": 443, "timeout": 5.0}]
    assert connect_host(_v4("8.8.8.8")) == "8.8.8.8"


def test_connect_tcp_never_passes_hostname_to_inner() -> None:
    resolver = FakeResolver([_v6("2001:4860:4860::8888")])
    inner = RecordingBackend()
    ValidatingNetworkBackend(resolver=resolver, inner=inner).connect_tcp(
        "opendata.example.gov", 443, timeout=5.0
    )
    assert inner.calls[0]["host"] == "2001:4860:4860::8888"
    assert inner.calls[0]["host"] != "opendata.example.gov"


def test_rejected_resolution_makes_zero_connect_calls() -> None:
    resolver = FakeResolver([_v4("8.8.8.8"), _v4("10.0.0.1")])
    inner = RecordingBackend()
    backend = ValidatingNetworkBackend(resolver=resolver, inner=inner)
    with pytest.raises(UnsafeResolvedAddressError):
        backend.connect_tcp("opendata.example.gov", 443, timeout=5.0)
    assert inner.calls == []


def test_dns_failure_makes_zero_connect_calls() -> None:
    resolver = FakeResolver(error=DnsResolutionError())
    inner = RecordingBackend()
    backend = ValidatingNetworkBackend(resolver=resolver, inner=inner)
    with pytest.raises(DnsResolutionError):
        backend.connect_tcp("opendata.example.gov", 443, timeout=5.0)
    assert inner.calls == []


def test_empty_resolver_result_makes_zero_connect_calls() -> None:
    resolver = FakeResolver([])
    inner = RecordingBackend()
    backend = ValidatingNetworkBackend(resolver=resolver, inner=inner)
    with pytest.raises(DnsResolutionError):
        backend.connect_tcp("opendata.example.gov", 443, timeout=5.0)
    assert inner.calls == []


def test_sequential_fallback_skips_failed_first_ip() -> None:
    resolver = FakeResolver([_v4("8.8.8.8"), _v4("1.1.1.1")])
    inner = RecordingBackend(errors=[httpcore.ConnectError("first")])
    stream = ValidatingNetworkBackend(resolver=resolver, inner=inner).connect_tcp(
        "opendata.example.gov", 443, timeout=5.0
    )
    assert [call["host"] for call in inner.calls] == ["8.8.8.8", "1.1.1.1"]
    assert isinstance(stream, httpcore.NetworkStream)


def test_only_approved_addresses_are_attempted() -> None:
    resolver = FakeResolver([_v4("8.8.8.8")])
    inner = RecordingBackend(errors=[httpcore.ConnectError("only")])
    backend = ValidatingNetworkBackend(resolver=resolver, inner=inner)
    with pytest.raises(httpcore.ConnectError):
        backend.connect_tcp("opendata.example.gov", 443, timeout=5.0)
    assert [call["host"] for call in inner.calls] == ["8.8.8.8"]


def test_connect_timeout_budget_shrinks_across_attempts() -> None:
    resolver = FakeResolver([_v4("8.8.8.8"), _v4("1.1.1.1")])
    clock = ManualClock(0.0)
    inner = RecordingBackend(
        errors=[httpcore.ConnectError("first")],
        clock=clock,
        advance=1.5,
    )
    backend = ValidatingNetworkBackend(resolver=resolver, inner=inner, monotonic=clock)
    backend.connect_tcp("opendata.example.gov", 443, timeout=5.0)
    assert inner.calls[0]["timeout"] == 5.0
    assert inner.calls[1]["timeout"] == pytest.approx(3.5)


def test_timeout_budget_exhaustion_prevents_another_connect() -> None:
    resolver = FakeResolver([_v4("8.8.8.8"), _v4("1.1.1.1")])
    clock = ManualClock(0.0)
    inner = RecordingBackend(
        errors=[httpcore.ConnectError("first")],
        clock=clock,
        advance=5.0,
    )
    backend = ValidatingNetworkBackend(resolver=resolver, inner=inner, monotonic=clock)
    with pytest.raises(httpcore.ConnectError):
        backend.connect_tcp("opendata.example.gov", 443, timeout=5.0)
    assert [call["host"] for call in inner.calls] == ["8.8.8.8"]


def test_unix_socket_is_rejected() -> None:
    inner = RecordingBackend()
    backend = ValidatingNetworkBackend(resolver=FakeResolver([_v4("8.8.8.8")]), inner=inner)
    with pytest.raises(UnsafeResolvedAddressError):
        backend.connect_unix_socket("/tmp/research.sock")
    assert inner.unix_paths == []
    assert inner.calls == []
