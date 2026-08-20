"""P4-B3 security headers, production HSTS, and /ready readiness tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from surplus_ai.api.app import create_app
from surplus_ai.api.dependencies import get_db_session
from surplus_ai.api.static import DASHBOARD_SECURITY_HEADERS, HSTS_HEADER_VALUE
from surplus_ai.utils.config import get_settings

VALID_URL = "postgresql+psycopg://u:p@localhost:5432/db"

REQUIRED_CSP = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'none'",
    "frame-ancestors 'none'",
)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _assert_browser_security_headers(response: object) -> None:
    headers = response.headers  # type: ignore[attr-defined]
    csp = headers["content-security-policy"]
    for directive in REQUIRED_CSP:
        assert directive in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    policy = headers["permissions-policy"]
    assert "camera=()" in policy
    assert "microphone=()" in policy
    assert "geolocation=()" in policy


def test_login_page_browser_security_headers(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/login")
    assert response.status_code == 200
    _assert_browser_security_headers(response)
    assert "strict-transport-security" not in {
        k.lower() for k in response.headers.keys()
    }


def test_static_asset_browser_security_headers(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/static/css/dashboard.css")
    assert response.status_code == 200
    _assert_browser_security_headers(response)


def test_root_redirect_browser_security_headers(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    _assert_browser_security_headers(response)


def test_dashboard_security_header_constants() -> None:
    assert DASHBOARD_SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in DASHBOARD_SECURITY_HEADERS["Content-Security-Policy"]
    assert HSTS_HEADER_VALUE == "max-age=31536000"


def test_dev_and_test_have_no_hsts(anonymous_client: TestClient) -> None:
    health = anonymous_client.get("/health")
    login = anonymous_client.get("/login")
    assert health.status_code == 200
    assert login.status_code == 200
    for response in (health, login):
        assert "strict-transport-security" not in {
            k.lower() for k in response.headers.keys()
        }


def test_production_hsts_on_health_and_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_AUTH_ORIGINS", "https://app.example.invalid")
    monkeypatch.setenv("SURPLUS_AI_ALLOWED_HOSTS", "app.example.invalid")
    application = create_app()
    with TestClient(application, base_url="http://app.example.invalid") as client:
        health = client.get("/health")
        status = client.get("/api/v1/status")
    assert health.status_code == 200
    assert health.headers["strict-transport-security"] == HSTS_HEADER_VALUE
    assert status.status_code == 401
    assert status.headers["strict-transport-security"] == HSTS_HEADER_VALUE
    # Production HSTS is env-driven, not request-scheme-driven (TestClient is HTTP).
    assert str(health.request.url).startswith("http://")


def test_invalid_host_still_400_without_weakening_trusted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_AUTH_ORIGINS", "https://app.example.invalid")
    monkeypatch.setenv("SURPLUS_AI_ALLOWED_HOSTS", "app.example.invalid")
    application = create_app()
    with TestClient(application, base_url="http://other.example.invalid") as client:
        response = client.get("/health")
    assert response.status_code == 400
    # TrustedHost rejects before dashboard middleware; HSTS not required here.
    assert "invalid host" in response.text.lower()


def test_ready_success(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}


def test_ready_failure_is_safe_503(session: Session) -> None:
    application = create_app()
    broken = MagicMock()
    broken.execute.side_effect = RuntimeError("simulated db failure")

    def _override() -> Iterator[Session]:
        yield broken

    application.dependency_overrides[get_db_session] = _override
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/ready")
    application.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    body = response.text.lower()
    assert "simulated" not in body
    assert "postgresql" not in body
    assert "password" not in body
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}


def test_health_stays_up_when_ready_fails(session: Session) -> None:
    application = create_app()
    broken = MagicMock()
    broken.execute.side_effect = RuntimeError("simulated db failure")

    def _override() -> Iterator[Session]:
        yield broken

    application.dependency_overrides[get_db_session] = _override
    with TestClient(application, raise_server_exceptions=False) as client:
        ready = client.get("/ready")
        health = client.get("/health")
    application.dependency_overrides.clear()
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
