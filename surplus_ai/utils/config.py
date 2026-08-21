from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import PrivateAttr, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from surplus_ai.utils.exceptions import AppError

DEFAULT_DEV_AUTH_ORIGIN = "http://127.0.0.1:8000"
DEFAULT_DEV_ALLOWED_HOSTS: tuple[str, ...] = ("127.0.0.1",)
DEFAULT_TEST_ALLOWED_HOSTS: tuple[str, ...] = ("127.0.0.1", "testserver")


class ConfigurationError(AppError):
    """Raised when application settings fail to load or fail validation."""


def _csv_tokens(raw: str) -> tuple[str, ...]:
    """Split a comma-separated env value. Empty tokens are rejected; duplicates keep first."""
    tokens: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        token = part.strip()
        if token == "":
            raise ValueError("comma-separated values must not contain empty tokens")
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    if not tokens:
        raise ValueError("comma-separated values must not be blank")
    return tuple(tokens)


def _validate_auth_origin(token: str, *, env: Literal["dev", "test", "prod"]) -> str:
    parsed = urlsplit(token)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("auth origin must be scheme://host[:port] with http or https")
    if env == "prod" and parsed.scheme != "https":
        raise ValueError("production auth origins must use https")
    if parsed.path not in {"",}:
        raise ValueError("auth origin must not include a path or trailing slash")
    if parsed.query:
        raise ValueError("auth origin must not include a query string")
    if parsed.fragment:
        raise ValueError("auth origin must not include a fragment")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("auth origin must not include userinfo")
    if parsed.hostname is None:
        raise ValueError("auth origin must include a host")
    host_part = parsed.netloc if parsed.port is None else parsed.netloc.rsplit(":", 1)[0]
    if host_part != host_part.lower():
        raise ValueError("auth origin host must be lowercase")
    if parsed.port in {80, 443}:
        raise ValueError("auth origin must omit default ports")
    canonical = f"{parsed.scheme}://{parsed.netloc}"
    if token != canonical:
        raise ValueError("auth origin must already be canonical scheme://host[:port]")
    return token


def _validate_allowed_host(token: str, *, env: Literal["dev", "test", "prod"]) -> str:
    if token != token.lower():
        raise ValueError("allowed host must be lowercase")
    if "*" in token:
        raise ValueError("wildcard allowed hosts are not permitted")
    if env == "prod" and token == "testserver":
        raise ValueError("production allowed hosts must not include testserver")
    if any(marker in token for marker in ("://", "/", "?", "#", "@", ":")):
        raise ValueError(
            "allowed host must be a hostname only (no scheme, path, port, or userinfo)"
        )
    if token.startswith(".") or token.endswith(".") or token == "":
        raise ValueError("allowed host is not a valid hostname")
    return token


def _resolve_auth_origins(
    raw: str | None, *, env: Literal["dev", "test", "prod"]
) -> tuple[str, ...]:
    if env == "prod":
        if raw is None or raw.strip() == "":
            raise ValueError("SURPLUS_AI_AUTH_ORIGINS is required when SURPLUS_AI_ENV=prod")
        return tuple(_validate_auth_origin(token, env=env) for token in _csv_tokens(raw))
    if raw is None:
        return (DEFAULT_DEV_AUTH_ORIGIN,)
    if raw.strip() == "":
        raise ValueError("SURPLUS_AI_AUTH_ORIGINS must not be blank")
    return tuple(_validate_auth_origin(token, env=env) for token in _csv_tokens(raw))


def _resolve_allowed_hosts(
    raw: str | None, *, env: Literal["dev", "test", "prod"]
) -> tuple[str, ...]:
    if env == "prod":
        if raw is None or raw.strip() == "":
            raise ValueError("SURPLUS_AI_ALLOWED_HOSTS is required when SURPLUS_AI_ENV=prod")
        return tuple(_validate_allowed_host(token, env=env) for token in _csv_tokens(raw))
    if raw is None:
        if env == "test":
            return DEFAULT_TEST_ALLOWED_HOSTS
        return DEFAULT_DEV_ALLOWED_HOSTS
    if raw.strip() == "":
        raise ValueError("SURPLUS_AI_ALLOWED_HOSTS must not be blank")
    return tuple(_validate_allowed_host(token, env=env) for token in _csv_tokens(raw))


def _validate_forwarded_allow_ip(token: str) -> str:
    """Accept only literal IP addresses or CIDR networks (Uvicorn 0.52.3-compatible).

    Rejects ``*`` and non-IP hostnames. Uvicorn would otherwise treat malformed
    entries as opaque literals; Holy Grail fails closed instead.
    """
    if token == "*":
        raise ValueError("wildcard forwarded allow IPs are not permitted")
    if "/" in token:
        try:
            return str(ipaddress.ip_network(token, strict=False))
        except ValueError as exc:
            raise ValueError(f"invalid trusted proxy network: {token}") from exc
    try:
        return str(ipaddress.ip_address(token))
    except ValueError as exc:
        raise ValueError(f"invalid trusted proxy address: {token}") from exc


def _resolve_forwarded_allow_ips(
    raw: str | None, *, trust_proxy_headers: bool
) -> tuple[str, ...]:
    if not trust_proxy_headers:
        return ()
    if raw is None or raw.strip() == "":
        raise ValueError(
            "SURPLUS_AI_FORWARDED_ALLOW_IPS is required when "
            "SURPLUS_AI_TRUST_PROXY_HEADERS is true"
        )
    return tuple(_validate_forwarded_allow_ip(token) for token in _csv_tokens(raw))


class Settings(BaseSettings):
    """Layered application configuration: env vars (SURPLUS_AI_*) override .env."""

    model_config = SettingsConfigDict(
        env_prefix="SURPLUS_AI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path = Path("logs")
    # Raw comma-separated strings. Stored as str so pydantic-settings does not JSON-decode.
    auth_origins: str | None = None
    allowed_hosts: str | None = None
    # Proxy trust is off by default. Uvicorn defaults proxy_headers=True; our
    # runtime launcher disables it unless these settings explicitly enable trust.
    trust_proxy_headers: bool = False
    forwarded_allow_ips: str | None = None

    _auth_origin_allowlist: tuple[str, ...] = PrivateAttr(default=())
    _allowed_host_allowlist: tuple[str, ...] = PrivateAttr(default=())
    _forwarded_allow_ip_allowlist: tuple[str, ...] = PrivateAttr(default=())

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use the 'postgresql+psycopg://' driver scheme")
        return value

    @model_validator(mode="after")
    def _validate_deployment_contract(self) -> Self:
        self._auth_origin_allowlist = _resolve_auth_origins(self.auth_origins, env=self.env)
        self._allowed_host_allowlist = _resolve_allowed_hosts(self.allowed_hosts, env=self.env)
        self._forwarded_allow_ip_allowlist = _resolve_forwarded_allow_ips(
            self.forwarded_allow_ips,
            trust_proxy_headers=self.trust_proxy_headers,
        )
        return self

    @property
    def auth_origin_allowlist(self) -> tuple[str, ...]:
        return self._auth_origin_allowlist

    @property
    def allowed_host_allowlist(self) -> tuple[str, ...]:
        return self._allowed_host_allowlist

    @property
    def forwarded_allow_ip_allowlist(self) -> tuple[str, ...]:
        return self._forwarded_allow_ip_allowlist


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton, loading it on first use."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(f"Failed to load application settings: {exc}") from exc
