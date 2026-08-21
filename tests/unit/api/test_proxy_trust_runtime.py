"""P4-C1 proxy trust Settings and Uvicorn ProxyHeadersMiddleware contract."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from surplus_ai.api.app import create_app
from surplus_ai.api.dependencies import get_db_session, get_writable_db_session
from surplus_ai.api.runtime import build_uvicorn_run_kwargs
from surplus_ai.utils.config import DEFAULT_DEV_AUTH_ORIGIN, Settings, get_settings

VALID_URL = "postgresql+psycopg://u:p@localhost:5432/db"
TRUSTED_PROXY = "10.0.0.1"
CLIENT_A = "192.0.2.10"
CLIENT_B = "192.0.2.11"
SPOOFED = "192.0.2.123"
UNTRUSTED_PEER = "198.51.100.50"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _echo_client_host_app(*, trusted_hosts: str | list[str]) -> Any:
    """ASGI app: ProxyHeadersMiddleware → echo request.client.host."""
    inner = FastAPI()

    @inner.get("/whoami")
    def whoami(request: Request) -> dict[str, str | None]:
        return {"client_host": request.client.host if request.client else None}

    return ProxyHeadersMiddleware(inner, trusted_hosts=trusted_hosts)


def test_default_proxy_trust_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SURPLUS_AI_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("SURPLUS_AI_FORWARDED_ALLOW_IPS", raising=False)
    settings = _settings(monkeypatch, SURPLUS_AI_ENV="dev")
    assert settings.trust_proxy_headers is False
    assert settings.forwarded_allow_ip_allowlist == ()


def test_trust_false_ignores_allowlist_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        SURPLUS_AI_ENV="dev",
        SURPLUS_AI_TRUST_PROXY_HEADERS="false",
        SURPLUS_AI_FORWARDED_ALLOW_IPS=TRUSTED_PROXY,
    )
    assert settings.trust_proxy_headers is False
    assert settings.forwarded_allow_ip_allowlist == ()


def test_trust_true_missing_allowlist_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SURPLUS_AI_FORWARDED_ALLOW_IPS", raising=False)
    with pytest.raises(ValidationError):
        _settings(monkeypatch, SURPLUS_AI_ENV="dev", SURPLUS_AI_TRUST_PROXY_HEADERS="true")


def test_trust_true_blank_allowlist_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_TRUST_PROXY_HEADERS="true",
            SURPLUS_AI_FORWARDED_ALLOW_IPS="   ",
        )


def test_wildcard_allowlist_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_TRUST_PROXY_HEADERS="true",
            SURPLUS_AI_FORWARDED_ALLOW_IPS="*",
        )


def test_wildcard_among_entries_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_TRUST_PROXY_HEADERS="true",
            SURPLUS_AI_FORWARDED_ALLOW_IPS=f"{TRUSTED_PROXY},*",
        )


def test_malformed_proxy_address_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_TRUST_PROXY_HEADERS="true",
            SURPLUS_AI_FORWARDED_ALLOW_IPS="not-an-ip",
        )


def test_exact_ip_and_cidr_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        SURPLUS_AI_ENV="dev",
        SURPLUS_AI_TRUST_PROXY_HEADERS="true",
        SURPLUS_AI_FORWARDED_ALLOW_IPS=f"{TRUSTED_PROXY}, 10.0.0.0/8, 2001:db8::1",
    )
    assert settings.trust_proxy_headers is True
    assert settings.forwarded_allow_ip_allowlist == (
        TRUSTED_PROXY,
        "10.0.0.0/8",
        "2001:db8::1",
    )


def test_runtime_kwargs_proxy_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SURPLUS_AI_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("SURPLUS_AI_FORWARDED_ALLOW_IPS", raising=False)
    _settings(monkeypatch, SURPLUS_AI_ENV="dev")
    kwargs = build_uvicorn_run_kwargs()
    assert kwargs["proxy_headers"] is False
    assert kwargs["forwarded_allow_ips"] == []
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8000
    assert kwargs["workers"] == 1
    assert kwargs["reload"] is False


def test_runtime_kwargs_proxy_enabled_with_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(
        monkeypatch,
        SURPLUS_AI_ENV="dev",
        SURPLUS_AI_TRUST_PROXY_HEADERS="true",
        SURPLUS_AI_FORWARDED_ALLOW_IPS=f"{TRUSTED_PROXY},10.0.0.0/8",
    )
    kwargs = build_uvicorn_run_kwargs()
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == [TRUSTED_PROXY, "10.0.0.0/8"]


def test_untrusted_peer_xff_spoof_ignored() -> None:
    """C1 commit blocker: untrusted peer + XFF must not rewrite client.host."""
    app = _echo_client_host_app(trusted_hosts=TRUSTED_PROXY)
    with TestClient(app, client=(UNTRUSTED_PEER, 50000)) as client:
        response = client.get("/whoami", headers={"X-Forwarded-For": SPOOFED})
    assert response.status_code == 200
    assert response.json()["client_host"] == UNTRUSTED_PEER


def test_trusted_proxy_rewrites_to_forwarded_client() -> None:
    app = _echo_client_host_app(trusted_hosts=TRUSTED_PROXY)
    with TestClient(app, client=(TRUSTED_PROXY, 50000)) as client:
        response = client.get("/whoami", headers={"X-Forwarded-For": CLIENT_A})
    assert response.status_code == 200
    assert response.json()["client_host"] == CLIENT_A


def test_trusted_proxy_xff_chain_first_untrusted_from_right() -> None:
    """Uvicorn 0.52.3: reverse-walk XFF; first untrusted hop is the client."""
    app = _echo_client_host_app(trusted_hosts=TRUSTED_PROXY)
    # Immediate peer is trusted. Rightmost XFF hop is an untrusted intermediate → that hop wins.
    xff = f"{CLIENT_A}, 198.51.100.99"
    with TestClient(app, client=(TRUSTED_PROXY, 50000)) as client:
        response = client.get("/whoami", headers={"X-Forwarded-For": xff})
    assert response.status_code == 200
    assert response.json()["client_host"] == "198.51.100.99"


def test_trusted_proxy_xff_chain_skips_trusted_hops() -> None:
    """When intermediate hops are also allowlisted, client is leftmost untrusted."""
    intermediate = "198.51.100.99"
    app = _echo_client_host_app(trusted_hosts=[TRUSTED_PROXY, intermediate])
    xff = f"{CLIENT_A}, {intermediate}"
    with TestClient(app, client=(TRUSTED_PROXY, 50000)) as client:
        response = client.get("/whoami", headers={"X-Forwarded-For": xff})
    assert response.status_code == 200
    assert response.json()["client_host"] == CLIENT_A


def test_trusted_proxy_cidr_rewrites_client() -> None:
    app = _echo_client_host_app(trusted_hosts="10.0.0.0/8")
    with TestClient(app, client=("10.1.2.3", 50000)) as client:
        response = client.get("/whoami", headers={"X-Forwarded-For": CLIENT_B})
    assert response.status_code == 200
    assert response.json()["client_host"] == CLIENT_B


def test_ipv6_trusted_proxy_and_client() -> None:
    proxy_v6 = "2001:db8::a"
    client_v6 = "2001:db8::b"
    app = _echo_client_host_app(trusted_hosts=proxy_v6)
    with TestClient(app, client=(proxy_v6, 50000)) as client:
        response = client.get("/whoami", headers={"X-Forwarded-For": client_v6})
    assert response.status_code == 200
    assert response.json()["client_host"] == client_v6


def test_trust_disabled_uvicorn_path_does_not_install_proxy_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When trust is off, runtime passes proxy_headers=False (no Uvicorn default trust)."""
    monkeypatch.delenv("SURPLUS_AI_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_ENV", "test")
    get_settings.cache_clear()
    kwargs = build_uvicorn_run_kwargs()
    assert kwargs["proxy_headers"] is False
    # Simulate untrusted path without middleware: peer host stays peer.
    app = FastAPI()

    @app.get("/whoami-raw")
    def whoami_raw(request: Request) -> dict[str, str | None]:
        return {"client_host": request.client.host if request.client else None}

    with TestClient(app, client=(UNTRUSTED_PEER, 50000)) as client:
        response = client.get("/whoami-raw", headers={"X-Forwarded-For": SPOOFED})
    assert response.status_code == 200
    assert response.json()["client_host"] == UNTRUSTED_PEER


def test_login_sees_distinct_forwarded_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trusted proxy + distinct XFF clients → throttle sees distinct request.client.host."""
    from sqlalchemy.orm import Session

    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_ENV", "test")
    monkeypatch.delenv("SURPLUS_AI_TRUST_PROXY_HEADERS", raising=False)
    get_settings.cache_clear()

    application = create_app()
    session = MagicMock(spec=Session)
    session.scalar.return_value = None

    def _override() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db_session] = _override
    application.dependency_overrides[get_writable_db_session] = _override

    wrapped = ProxyHeadersMiddleware(application, trusted_hosts=TRUSTED_PROXY)
    open_decision = MagicMock(blocked=False, retry_after_seconds=None)

    with (
        patch("surplus_ai.api.routes.auth.consume_ip_login_attempt") as consume_ip,
        patch("surplus_ai.api.routes.auth.precheck_credential_throttle") as precheck,
        patch("surplus_ai.api.routes.auth.run_unknown_user_password_check"),
        patch("surplus_ai.api.routes.auth.record_credential_failure"),
    ):
        consume_ip.return_value = open_decision
        precheck.return_value = open_decision

        with TestClient(
            wrapped,
            raise_server_exceptions=False,
            client=(TRUSTED_PROXY, 50000),
        ) as client:
            for client_ip in (CLIENT_A, CLIENT_B):
                client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": "operator@example.invalid",
                        "password": "wrong-password!!",
                    },
                    headers={
                        "Origin": DEFAULT_DEV_AUTH_ORIGIN,
                        "X-Forwarded-For": client_ip,
                    },
                )

    assert consume_ip.call_count == 2
    assert consume_ip.call_args_list[0].kwargs["canonical_ip"] == CLIENT_A
    assert consume_ip.call_args_list[1].kwargs["canonical_ip"] == CLIENT_B
    assert (
        consume_ip.call_args_list[0].kwargs["canonical_ip"]
        != consume_ip.call_args_list[1].kwargs["canonical_ip"]
    )
