from __future__ import annotations

import ssl
from typing import Any

import httpx
import pytest

from surplus_ai.research.http import (
    DEFAULT_USER_AGENT,
    ResearchHttpClient,
    _is_tls_failure,
)


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
