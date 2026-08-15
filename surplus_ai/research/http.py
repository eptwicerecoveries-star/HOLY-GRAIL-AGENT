"""Synchronous research HTTP client. Does not retry. Does not follow redirects."""

from __future__ import annotations

import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

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


class ResearchHttpClient:
    """GET-only client: HTTPS only, TLS verify on, no retries, no redirect following."""

    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        transport: httpx.BaseTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
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
        if transport is None:
            transport = httpx.HTTPTransport(verify=True, retries=0)
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

        merged = {str(key): str(value) for key, value in headers.items()}
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
