from __future__ import annotations

import ipaddress
import ssl
from typing import Any

import httpcore
import httpx
import pytest

from surplus_ai.research.dns import DnsResolutionError, UnsafeResolvedAddressError
from surplus_ai.research.http import (
    DEFAULT_USER_AGENT,
    PinnedHttpsTransport,
    ResearchHttpClient,
    _is_tls_failure,
)
from tests.unit.research.test_dns import FakeResolver, RecordingBackend


def _client(handler: Any, **kwargs: Any) -> ResearchHttpClient:
    return ResearchHttpClient(transport=httpx.MockTransport(handler), **kwargs)


def test_trust_env_disabled_and_redirects_off() -> None:
    client = ResearchHttpClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    try:
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert client._client.headers["User-Agent"] == DEFAULT_USER_AGENT
    finally:
        client.close()


def test_https_json_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.scheme == "https"
        return httpx.Response(200, json=[{"parcel_id": "1"}])

    client = _client(handler)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={"$limit": "1"},
            headers={},
        )
    finally:
        client.close()
    assert result.error_code is None
    assert result.status_code == 200
    assert b"parcel_id" in result.body


def test_streaming_max_response_cap_aborts_and_drops_body() -> None:
    oversized = b"x" * (1024 * 1024 + 64)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    client = _client(handler, max_bytes=1024)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={},
        )
    finally:
        client.close()
    assert result.error_code == "response_too_large"
    assert result.retryable is False
    assert result.body == b""


def test_redirect_not_followed() -> None:
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        return httpx.Response(
            302,
            headers={"Location": "https://evil.example/resource/abcd-1234.json"},
        )

    client = _client(handler)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={},
        )
    finally:
        client.close()
    assert seen["n"] == 1
    assert result.error_code == "http_redirect"
    assert result.retryable is False
    assert result.body == b""


@pytest.mark.parametrize(
    "url",
    [
        "http://opendata.example.gov/resource/abcd-1234.json",
        "file:///tmp/x",
        "ftp://opendata.example.gov/x",
        "https://localhost/resource/abcd-1234.json",
        "https://127.0.0.1/resource/abcd-1234.json",
    ],
)
def test_client_rejects_unsafe_urls_without_transport(url: str) -> None:
    called = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json=[])

    client = _client(handler)
    try:
        result = client.get(url, params={}, headers={})
    finally:
        client.close()
    assert called["n"] == 0
    assert result.error_code == "unsafe_url"
    assert result.retryable is False


def test_timeout_maps_to_retryable_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read")

    client = _client(handler)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={},
        )
    finally:
        client.close()
    assert result.error_code == "timeout"
    assert result.retryable is True


def test_connect_failure_is_retryable_network_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed")

    client = _client(handler)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={},
        )
    finally:
        client.close()
    assert result.error_code == "network_failure"
    assert result.retryable is True


def test_tls_certificate_failure_is_not_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect") from ssl.SSLCertVerificationError(
            "certificate verify failed"
        )

    client = _client(handler)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={},
        )
    finally:
        client.close()
    assert result.error_code == "tls_failure"
    assert result.retryable is False


def test_is_tls_failure_requires_ssl_exception_type() -> None:
    plain = httpx.ConnectError("connection failed")
    assert _is_tls_failure(plain) is False
    wrapped = httpx.ConnectError("connect")
    wrapped.__cause__ = ssl.SSLCertVerificationError("bad cert")
    assert _is_tls_failure(wrapped) is True


def test_query_params_are_encoded_by_httpx() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=[])

    client = _client(handler)
    try:
        client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={"$where": "parcel_id = 'O''HARA'", "$limit": "10"},
            headers={},
        )
    finally:
        client.close()
    assert captured["url"].startswith("https://opendata.example.gov/resource/abcd-1234.json?")
    assert "parcel_id" in captured["url"]
    assert "'" not in captured["url"] or "%27" in captured["url"] or "%22" in captured["url"]


def test_caller_host_header_cannot_override_url_host() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.headers["Host"]
        return httpx.Response(200, json=[])

    client = _client(handler)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={"Host": "evil.example", "X-App-Token": "super-secret-app-token"},
        )
    finally:
        client.close()
    assert result.error_code is None
    assert captured["host"] == "opendata.example.gov"


def test_default_client_uses_pinned_transport() -> None:
    client = ResearchHttpClient()
    try:
        assert isinstance(client.pinned_transport, PinnedHttpsTransport)
        transport = client.pinned_transport
        assert transport.http2 is False
        assert transport.retries == 0
        assert transport.max_keepalive_connections == 0
        assert transport.keepalive_expiry == 0.0
        assert transport.ssl_context.verify_mode != ssl.CERT_NONE
        assert transport.ssl_context.check_hostname is True
        assert client.trust_env is False
        assert client.follow_redirects is False
    finally:
        client.close()


def test_injected_transport_is_explicit_unpinned_test_seam() -> None:
    client = ResearchHttpClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    try:
        assert client.pinned_transport is None
    finally:
        client.close()


class _RecordingStream(httpcore.NetworkStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._inner = httpcore.MockStream(chunks)
        self.server_hostname: str | None = None
        self.writes: list[bytes] = []

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)
        self._inner.write(buffer, timeout)

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        self.server_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


def _http11_response(body: bytes, *, status: int = 200) -> bytes:
    return (
        f"HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


def _pinned_client(
    resolver: FakeResolver,
    stream: _RecordingStream,
    inner: RecordingBackend | None = None,
) -> tuple[ResearchHttpClient, RecordingBackend]:
    backend = inner or RecordingBackend(stream=stream)
    client = ResearchHttpClient(resolver=resolver, network_backend=backend)
    return client, backend


def test_pinned_request_keeps_hostname_for_host_and_sni() -> None:
    resolver = FakeResolver([ipaddress.IPv4Address("8.8.8.8")])
    stream = _RecordingStream([_http11_response(b"[]")])
    client, inner = _pinned_client(resolver, stream)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={"$where": "parcel_id = '01-234567'", "$limit": "10"},
            headers={"X-App-Token": "super-secret-app-token", "Host": "evil.example"},
        )
    finally:
        client.close()
    assert result.error_code is None
    assert result.status_code == 200
    assert inner.calls[0]["host"] == "8.8.8.8"
    assert inner.calls[0]["host"] != "opendata.example.gov"
    assert stream.server_hostname == "opendata.example.gov"
    assert stream.server_hostname != "8.8.8.8"
    request_bytes = b"".join(stream.writes).lower()
    assert b"host: opendata.example.gov" in request_bytes
    assert b"evil.example" not in request_bytes
    assert resolver.calls == [("opendata.example.gov", 443)]
    assert resolver.calls[0][0] != "super-secret-app-token"
    assert "parcel_id" not in resolver.calls[0][0]
    assert "$where" not in str(resolver.calls)


def test_pinned_streaming_cap_still_drops_oversized_body() -> None:
    oversized = b"x" * (1024 * 1024 + 64)
    resolver = FakeResolver([ipaddress.IPv4Address("8.8.8.8")])
    stream = _RecordingStream([_http11_response(oversized)])
    client, _inner = _pinned_client(resolver, stream)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={},
        )
    finally:
        client.close()
    assert result.error_code == "response_too_large"
    assert result.retryable is False
    assert result.body == b""


def test_dns_resolution_failed_is_retryable_without_connect() -> None:
    resolver = FakeResolver(error=DnsResolutionError())
    inner = RecordingBackend()
    client = ResearchHttpClient(resolver=resolver, network_backend=inner)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={"$where": "parcel_id = 'SECRET'"},
            headers={"X-App-Token": "super-secret-app-token"},
        )
    finally:
        client.close()
    assert result.error_code == "dns_resolution_failed"
    assert result.retryable is True
    assert result.body == b""
    assert inner.calls == []
    assert "SECRET" not in str(result)
    assert "super-secret" not in str(result)


def test_unsafe_resolved_address_is_not_retryable_without_connect() -> None:
    resolver = FakeResolver(
        [ipaddress.IPv4Address("8.8.8.8"), ipaddress.IPv4Address("10.0.0.1")]
    )
    inner = RecordingBackend()
    client = ResearchHttpClient(resolver=resolver, network_backend=inner)
    try:
        result = client.get(
            "https://opendata.example.gov/resource/abcd-1234.json",
            params={},
            headers={},
        )
    finally:
        client.close()
    assert result.error_code == "unsafe_resolved_address"
    assert result.retryable is False
    assert inner.calls == []
    assert str(UnsafeResolvedAddressError()) == "unsafe_resolved_address"


def test_unsafe_url_does_not_call_resolver() -> None:
    resolver = FakeResolver([ipaddress.IPv4Address("8.8.8.8")])
    inner = RecordingBackend()
    client = ResearchHttpClient(resolver=resolver, network_backend=inner)
    try:
        result = client.get("https://127.0.0.1/resource/abcd-1234.json", params={}, headers={})
    finally:
        client.close()
    assert result.error_code == "unsafe_url"
    assert resolver.calls == []
    assert inner.calls == []
