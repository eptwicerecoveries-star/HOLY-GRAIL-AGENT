"""Synchronous research HTTP client. Does not retry. Does not follow redirects.

Production/default construction uses ``PinnedHttpsTransport``: DNS is resolved
and validated, then TCP connects only to those numeric addresses while TLS/SNI/Host
stay bound to the original hostname. Passing ``transport=`` is a unit-test seam
and bypasses DNS pinning; it is not selectable from provider YAML.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import NoReturn, Protocol

import httpcore
import httpx
import structlog

from surplus_ai.research.dns import (
    DnsResolutionError,
    IpAddress,
    Resolver,
    SystemResolver,
    UnsafeResolvedAddressError,
    connect_host,
    destination_is_unsafe,
    validate_resolved_addresses,
)
from surplus_ai.research.endpoint import validate_public_https_url
from surplus_ai.research.exceptions import ResearchConfigError

logger = structlog.get_logger(__name__)

DEFAULT_MAX_BYTES = 1 * 1024 * 1024
DEFAULT_USER_AGENT = "SurplusAI-Research/0.1"
_CHUNK_SIZE = 64 * 1024

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0
WRITE_TIMEOUT_SECONDS = 5.0
POOL_TIMEOUT_SECONDS = 5.0

MonotonicFn = Callable[[], float]


@dataclass(frozen=True)
class HttpGetResult:
    """Transport result. Body is empty when an error_code is set for safety/size."""

    status_code: int | None = None
    body: bytes = b""
    error_code: str | None = None
    retryable: bool = False


class HttpGetter(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpGetResult: ...


def _is_tls_failure(exc: BaseException) -> bool:
    """True when a public ssl exception type is in the cause chain. No string matching."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _headers_without_host(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key): str(value) for key, value in headers.items() if str(key).lower() != "host"}


def _ip_literal_or_none(host: str) -> IpAddress | None:
    import ipaddress

    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


class ValidatingNetworkBackend(httpcore.NetworkBackend):
    """Resolve + validate, then TCP-connect only to approved numeric addresses."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        inner: httpcore.NetworkBackend | None = None,
        monotonic: MonotonicFn | None = None,
        default_connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._resolver: Resolver = resolver or SystemResolver()
        self._inner: httpcore.NetworkBackend = inner or httpcore.SyncBackend()
        self._monotonic: MonotonicFn = monotonic or time.monotonic
        self._default_connect_timeout = default_connect_timeout

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        approved = self._approved_addresses(host, port)
        budget = self._default_connect_timeout if timeout is None else timeout
        deadline = self._monotonic() + budget
        last_connect_error: BaseException | None = None
        for address in approved:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                if last_connect_error is not None:
                    raise last_connect_error
                raise httpcore.ConnectTimeout("Connect timeout")
            try:
                return self._inner.connect_tcp(
                    connect_host(address),
                    port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_connect_error = exc
        if last_connect_error is not None:
            raise last_connect_error
        raise httpcore.ConnectError("Connect failed")

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        raise UnsafeResolvedAddressError()

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)

    def _approved_addresses(self, host: str, port: int) -> tuple[IpAddress, ...]:
        literal = _ip_literal_or_none(host)
        if literal is not None:
            if destination_is_unsafe(literal):
                raise UnsafeResolvedAddressError()
            return (literal,)
        resolved = self._resolver.resolve(host, port)
        approved = validate_resolved_addresses(resolved)
        logger.info(
            "research_dns_validated",
            hostname=host,
            address_count=len(approved),
        )
        return approved


class _CoreResponseStream(httpx.SyncByteStream):
    """Pass-through httpcore body iterator. Does not buffer the full response."""

    def __init__(self, httpcore_stream: Iterable[bytes]) -> None:
        self._httpcore_stream = httpcore_stream

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._httpcore_stream
        except (
            DnsResolutionError,
            UnsafeResolvedAddressError,
            httpcore.NetworkError,
            httpcore.TimeoutException,
            httpcore.ProtocolError,
            httpcore.ProxyError,
            httpcore.UnsupportedProtocol,
        ) as exc:
            _reraise_mapped(exc)

    def close(self) -> None:
        closer = getattr(self._httpcore_stream, "close", None)
        if closer is not None:
            closer()


def _reraise_mapped(exc: BaseException) -> NoReturn:
    if isinstance(exc, DnsResolutionError | UnsafeResolvedAddressError):
        raise exc
    mapped = _map_httpcore_exception(exc)
    if mapped is not None:
        raise mapped from exc
    raise exc


def _map_httpcore_exception(exc: BaseException) -> httpx.HTTPError | None:
    mapping: tuple[tuple[type[BaseException], type[httpx.HTTPError]], ...] = (
        (httpcore.ConnectTimeout, httpx.ConnectTimeout),
        (httpcore.ReadTimeout, httpx.ReadTimeout),
        (httpcore.WriteTimeout, httpx.WriteTimeout),
        (httpcore.PoolTimeout, httpx.PoolTimeout),
        (httpcore.ConnectError, httpx.ConnectError),
        (httpcore.ReadError, httpx.ReadError),
        (httpcore.WriteError, httpx.WriteError),
        (httpcore.ProxyError, httpx.ProxyError),
        (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
        (httpcore.LocalProtocolError, httpx.LocalProtocolError),
        (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
        (httpcore.ProtocolError, httpx.ProtocolError),
        (httpcore.TimeoutException, httpx.TimeoutException),
        (httpcore.NetworkError, httpx.NetworkError),
    )
    for core_type, httpx_type in mapping:
        if isinstance(exc, core_type):
            return httpx_type(str(exc))
    return None


class PinnedHttpsTransport(httpx.BaseTransport):
    """HTTPS-only transport: validated DNS set, no proxy, no HTTP/2, no keepalive."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        network_backend: httpcore.NetworkBackend | None = None,
        monotonic: MonotonicFn | None = None,
    ) -> None:
        self.ssl_context = httpx.create_ssl_context(verify=True, trust_env=False)
        self.http2 = False
        self.retries = 0
        self.max_keepalive_connections = 0
        self.keepalive_expiry = 0.0
        self.network_backend = ValidatingNetworkBackend(
            resolver=resolver,
            inner=network_backend,
            monotonic=monotonic,
        )
        self._pool = httpcore.ConnectionPool(
            ssl_context=self.ssl_context,
            max_connections=1,
            max_keepalive_connections=self.max_keepalive_connections,
            keepalive_expiry=self.keepalive_expiry,
            http1=True,
            http2=self.http2,
            retries=self.retries,
            network_backend=self.network_backend,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https":
            raise UnsafeResolvedAddressError()
        if not isinstance(request.stream, httpx.SyncByteStream):
            raise TypeError("PinnedHttpsTransport requires a synchronous request stream")
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = self._pool.handle_request(core_request)
        except (
            DnsResolutionError,
            UnsafeResolvedAddressError,
            httpcore.NetworkError,
            httpcore.TimeoutException,
            httpcore.ProtocolError,
            httpcore.ProxyError,
            httpcore.UnsupportedProtocol,
        ) as exc:
            _reraise_mapped(exc)
        stream = response.stream
        if not isinstance(stream, Iterable):
            raise TypeError("PinnedHttpsTransport requires a synchronous response stream")
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreResponseStream(stream),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._pool.close()


class ResearchHttpClient:
    """GET-only client: HTTPS only, TLS verify on, no retries, no redirect following."""

    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        transport: httpx.BaseTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        resolver: Resolver | None = None,
        network_backend: httpcore.NetworkBackend | None = None,
        monotonic: MonotonicFn | None = None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be >= 1")
        self._max_bytes = max_bytes
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_SECONDS,
            read=READ_TIMEOUT_SECONDS,
            write=WRITE_TIMEOUT_SECONDS,
            pool=POOL_TIMEOUT_SECONDS,
        )
        # ``transport=`` is an explicit unit-test seam and bypasses DNS pinning.
        # Production/default construction always uses PinnedHttpsTransport.
        self.pinned_transport: PinnedHttpsTransport | None
        if transport is None:
            self.pinned_transport = PinnedHttpsTransport(
                resolver=resolver,
                network_backend=network_backend,
                monotonic=monotonic,
            )
            transport = self.pinned_transport
        else:
            self.pinned_transport = None
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            verify=True,
            transport=transport,
            headers={"User-Agent": user_agent},
        )

    @property
    def trust_env(self) -> bool:
        return bool(self._client.trust_env)

    @property
    def follow_redirects(self) -> bool:
        return bool(self._client.follow_redirects)

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpGetResult:
        try:
            validate_public_https_url(url)
        except ResearchConfigError:
            logger.info("research_http_unsafe_url")
            return HttpGetResult(error_code="unsafe_url", retryable=False)

        merged = _headers_without_host(headers)
        try:
            with self._client.stream("GET", url, params=dict(params), headers=merged) as response:
                if 300 <= response.status_code < 400:
                    response.close()
                    return HttpGetResult(
                        status_code=response.status_code,
                        body=b"",
                        error_code="http_redirect",
                        retryable=False,
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self._max_bytes:
                        response.close()
                        logger.info(
                            "research_http_response_too_large",
                            status_code=response.status_code,
                            max_bytes=self._max_bytes,
                        )
                        return HttpGetResult(
                            status_code=response.status_code,
                            body=b"",
                            error_code="response_too_large",
                            retryable=False,
                        )
                    chunks.append(chunk)
                return HttpGetResult(
                    status_code=response.status_code,
                    body=b"".join(chunks),
                    error_code=None,
                    retryable=False,
                )
        except DnsResolutionError:
            logger.info("research_dns_resolution_failed")
            return HttpGetResult(error_code="dns_resolution_failed", retryable=True)
        except UnsafeResolvedAddressError:
            logger.info("research_dns_unsafe_resolved_address")
            return HttpGetResult(error_code="unsafe_resolved_address", retryable=False)
        except httpx.TimeoutException:
            return HttpGetResult(error_code="timeout", retryable=True)
        except httpx.ConnectError as exc:
            if _is_tls_failure(exc):
                return HttpGetResult(error_code="tls_failure", retryable=False)
            return HttpGetResult(error_code="network_failure", retryable=True)
        except ssl.SSLError:
            return HttpGetResult(error_code="tls_failure", retryable=False)
        except httpx.NetworkError as exc:
            if _is_tls_failure(exc):
                return HttpGetResult(error_code="tls_failure", retryable=False)
            return HttpGetResult(error_code="network_failure", retryable=True)
        except httpx.TransportError:
            # Ambiguous transport failure: fail closed rather than retry.
            return HttpGetResult(error_code="network_failure", retryable=False)
