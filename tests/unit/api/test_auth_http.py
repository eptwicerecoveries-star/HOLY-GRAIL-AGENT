"""P3-B2 HTTP login, cookie, origin, and route-protection tests."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.api.app import create_app
from surplus_ai.api.dependencies import get_db_session, get_writable_db_session
from surplus_ai.api.session_cookie import (
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
    session_cookie_secure,
)
from surplus_ai.auth.passwords import hash_password
from surplus_ai.auth.sessions import digest_session_token
from surplus_ai.database.models.auth_session import AuthSession
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.user import User
from tests.unit.api.conftest import AUTH_ORIGIN_HEADERS, TEST_PASSWORD

_GENERIC_FAILURE = {
    "code": "authentication_failed",
    "message": "Invalid email or password.",
}


def _set_cookie_header(response: object) -> str:
    headers = response.headers  # type: ignore[attr-defined]
    raw = headers.get("set-cookie")
    assert raw is not None
    return raw


def _cookie_value(set_cookie: str) -> str:
    match = re.match(rf"{SESSION_COOKIE_NAME}=([^;]+)", set_cookie)
    assert match is not None
    return match.group(1)


def test_session_cookie_secure_policy() -> None:
    assert session_cookie_secure(env="dev") is False
    assert session_cookie_secure(env="test") is False
    assert session_cookie_secure(env="prod") is True


def test_login_sets_httponly_cookie_and_session_row(
    anonymous_client: TestClient, password_user: User, session: Session
) -> None:
    response = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": password_user.email, "password": TEST_PASSWORD},
        headers=AUTH_ORIGIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(password_user.id)
    assert body["email"] == password_user.email
    assert body["role"] == "agent"
    assert body["is_active"] is True
    assert "password_hash" not in body
    assert "token" not in body
    assert "raw_token" not in body
    assert "token_digest" not in body

    set_cookie = _set_cookie_header(response)
    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "Path=/" in set_cookie or "path=/" in set_cookie.lower()
    assert f"Max-Age={SESSION_COOKIE_MAX_AGE}" in set_cookie or (
        f"max-age={SESSION_COOKIE_MAX_AGE}" in set_cookie.lower()
    )
    assert "Domain=" not in set_cookie
    assert "secure" not in set_cookie.lower()  # test/dev Secure=False

    raw_token = _cookie_value(set_cookie)
    assert raw_token not in response.text
    row = session.scalar(select(AuthSession).where(AuthSession.user_id == password_user.id))
    assert row is not None
    assert row.token_digest == digest_session_token(raw_token)
    assert raw_token not in row.token_digest


@pytest.mark.parametrize(
    "email,password,setup",
    [
        ("missing@example.invalid", TEST_PASSWORD, None),
        ("operator@example.invalid", "wrong-password!!!!", "active"),
        ("nullhash@example.invalid", TEST_PASSWORD, "null_hash"),
        ("inactive@example.invalid", TEST_PASSWORD, "inactive"),
    ],
)
def test_login_failure_matrix_is_identical(
    anonymous_client: TestClient,
    session: Session,
    password_user: User,
    email: str,
    password: str,
    setup: str | None,
) -> None:
    _ = password_user
    if setup == "null_hash":
        session.add(
            User(
                name="Null Hash",
                email="nullhash@example.invalid",
                role=UserRole.AGENT,
                is_active=True,
                password_hash=None,
            )
        )
        session.flush()
    elif setup == "inactive":
        session.add(
            User(
                name="Inactive",
                email="inactive@example.invalid",
                role=UserRole.AGENT,
                is_active=False,
                password_hash=hash_password(TEST_PASSWORD),
            )
        )
        session.flush()

    before = session.scalar(select(func.count()).select_from(AuthSession)) or 0
    response = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers=AUTH_ORIGIN_HEADERS,
    )
    assert response.status_code == 401
    assert response.json() == _GENERIC_FAILURE
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()} or (
        SESSION_COOKIE_NAME not in (response.headers.get("set-cookie") or "")
    )
    assert session.scalar(select(func.count()).select_from(AuthSession)) == before
    assert "password_hash" not in response.text
    assert "$argon2" not in response.text.lower()


def test_login_unknown_email_runs_dummy_verify(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _spy(password: str, password_hash: str) -> bool:
        calls.append("verify")
        from surplus_ai.auth.passwords import verify_password as real

        return real(password, password_hash)

    monkeypatch.setattr("surplus_ai.api.auth_timing.verify_password", _spy)
    response = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.invalid", "password": TEST_PASSWORD},
        headers=AUTH_ORIGIN_HEADERS,
    )
    assert response.status_code == 401
    assert calls == ["verify"]


def test_login_origin_required(anonymous_client: TestClient, password_user: User) -> None:
    missing = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": password_user.email, "password": TEST_PASSWORD},
    )
    assert missing.status_code == 403
    assert missing.json()["code"] == "origin_not_allowed"

    wrong = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": password_user.email, "password": TEST_PASSWORD},
        headers={"Origin": "http://localhost:8000"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "origin_not_allowed"
    assert "localhost" not in wrong.json()["message"]

    evil = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": password_user.email, "password": TEST_PASSWORD},
        headers={"Origin": "http://evil.example"},
    )
    assert evil.status_code == 403


def test_logout_revokes_and_is_idempotent(
    api_client: TestClient, password_user: User, session: Session
) -> None:
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 1
    first = api_client.post("/api/v1/auth/logout", headers=AUTH_ORIGIN_HEADERS)
    assert first.status_code == 200
    assert first.json() == {"status": "ok"}
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
    set_cookie = _set_cookie_header(first)
    assert SESSION_COOKIE_NAME in set_cookie
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()

    protected = api_client.get("/api/v1/status")
    assert protected.status_code == 401
    assert protected.json()["code"] == "authentication_required"

    second = api_client.post("/api/v1/auth/logout", headers=AUTH_ORIGIN_HEADERS)
    assert second.status_code == 200
    assert second.json() == {"status": "ok"}


def test_logout_commit_failure_does_not_clear_cookie(
    password_user: User,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed revocation commit must not look like a successful logout."""
    application = create_app()

    def _override() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db_session] = _override
    application.dependency_overrides[get_writable_db_session] = _override

    def _fail_commit() -> None:
        raise RuntimeError("simulated logout commit failure")

    # raise_server_exceptions=False so we observe the generic 500 envelope.
    with TestClient(application, raise_server_exceptions=False) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": password_user.email, "password": TEST_PASSWORD},
            headers=AUTH_ORIGIN_HEADERS,
        )
        assert login.status_code == 200
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 1

        monkeypatch.setattr(session, "commit", _fail_commit)
        response = client.post("/api/v1/auth/logout", headers=AUTH_ORIGIN_HEADERS)

    application.dependency_overrides.clear()

    assert response.status_code == 500
    body = response.json()
    assert body == {"code": "internal_error", "message": "Internal server error"}
    blob = response.text.lower()
    assert "traceback" not in blob
    assert "postgresql" not in blob
    assert "password" not in blob
    assert "surplus_ai_session" not in blob
    assert "$argon2" not in blob
    # Cookie clear runs only after a successful commit path.
    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is None or SESSION_COOKIE_NAME not in set_cookie
    # Do not assert AuthSession row survival here: the API test fixture overrides
    # get_writable_db_session without the production rollback wrapper, so flush
    # effects after a monkeypatched commit failure are not a reliable production
    # signal under this nested-savepoint architecture.


def test_logout_without_cookie_is_safe(anonymous_client: TestClient) -> None:
    response = anonymous_client.post("/api/v1/auth/logout", headers=AUTH_ORIGIN_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_logout_rejects_bad_origin(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/auth/logout", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_me_requires_auth_and_hides_secrets(
    api_client: TestClient, anonymous_client: TestClient, password_user: User, session: Session
) -> None:
    anon = anonymous_client.get("/api/v1/auth/me")
    assert anon.status_code == 401
    assert anon.json()["code"] == "authentication_required"

    ok = api_client.get("/api/v1/auth/me")
    assert ok.status_code == 200
    body = ok.json()
    assert body["id"] == str(password_user.id)
    assert body["email"] == password_user.email
    assert "password_hash" not in body
    assert "token" not in body
    assert "token_digest" not in body

    password_user.is_active = False
    session.flush()
    inactive = api_client.get("/api/v1/auth/me")
    assert inactive.status_code == 401


def test_protected_api_requires_auth(anonymous_client: TestClient) -> None:
    for path in (
        "/api/v1/status",
        "/api/v1/cases",
        "/api/v1/leads",
        "/api/v1/research/reviews",
        "/api/v1/contacts",
    ):
        response = anonymous_client.get(path)
        assert response.status_code == 401, path
        assert response.json() == {
            "code": "authentication_required",
            "message": "Authentication is required.",
        }


def test_health_and_docs_remain_public(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/health").status_code == 200
    assert anonymous_client.get("/docs").status_code == 200
    assert anonymous_client.get("/openapi.json").status_code == 200
    assert anonymous_client.get("/redoc").status_code == 200


def test_dashboard_anonymous_redirects_login(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_authenticated_serves_html(api_client: TestClient) -> None:
    response = api_client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "Holy Grail" in response.text
    assert "Log out" in response.text


def test_login_page_and_authenticated_redirect(
    anonymous_client: TestClient, api_client: TestClient
) -> None:
    page = anonymous_client.get("/login", follow_redirects=False)
    assert page.status_code == 200
    assert "Sign in" in page.text
    assert "/static/js/login.js" in page.text
    assert 'type="password"' in page.text

    redirect = api_client.get("/login", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/"


def test_static_assets_remain_public(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/static/css/dashboard.css").status_code == 200
    assert anonymous_client.get("/static/js/login.js").status_code == 200
    assert anonymous_client.get("/static/js/api.js").status_code == 200


def test_frontend_auth_contract() -> None:
    from surplus_ai.api.static import STATIC_DIR

    login_js = (STATIC_DIR / "js" / "login.js").read_text(encoding="utf-8")
    api_js = (STATIC_DIR / "js" / "api.js").read_text(encoding="utf-8")
    dashboard_js = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert '"/api/v1/auth/login"' in login_js
    assert "method: \"POST\"" in login_js
    assert "localStorage" not in login_js
    assert "sessionStorage" not in login_js
    assert "document.cookie" not in login_js
    assert "status === 401" in api_js
    assert '"/login"' in api_js
    assert "/api/v1/auth/logout" in dashboard_js
    assert "cdn.jsdelivr.net" not in login_js


def test_prod_cookie_sets_secure(session: Session, password_user: User) -> None:
    application = create_app()

    def _override() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db_session] = _override
    application.dependency_overrides[get_writable_db_session] = _override
    with (
        patch("surplus_ai.api.routes.auth.get_settings") as mock_settings,
        TestClient(application) as client,
    ):
        mock_settings.return_value.env = "prod"
        response = client.post(
            "/api/v1/auth/login",
            json={"email": password_user.email, "password": TEST_PASSWORD},
            headers=AUTH_ORIGIN_HEADERS,
        )
    assert response.status_code == 200
    assert "Secure" in _set_cookie_header(response)
    application.dependency_overrides.clear()


def test_expired_session_is_rejected(
    api_client: TestClient, password_user: User, session: Session
) -> None:
    row = session.scalar(select(AuthSession).where(AuthSession.user_id == password_user.id))
    assert row is not None
    row.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    session.flush()
    response = api_client.get("/api/v1/status")
    assert response.status_code == 401
    assert session.get(AuthSession, row.id) is not None


def test_login_preserves_exact_email_match(
    anonymous_client: TestClient, password_user: User
) -> None:
    # Stored email is lower-case fixture; altered case must not match.
    altered = password_user.email.upper()
    assert altered != password_user.email
    response = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": altered, "password": TEST_PASSWORD},
        headers=AUTH_ORIGIN_HEADERS,
    )
    assert response.status_code == 401
    assert response.json() == _GENERIC_FAILURE


def test_multiple_logins_create_independent_sessions(
    anonymous_client: TestClient, password_user: User, session: Session
) -> None:
    first = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": password_user.email, "password": TEST_PASSWORD},
        headers=AUTH_ORIGIN_HEADERS,
    )
    assert first.status_code == 200
    # Second client jar for a second session without clearing the first DB row.
    application = create_app()

    def _override() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db_session] = _override
    application.dependency_overrides[get_writable_db_session] = _override
    with TestClient(application) as second_client:
        second = second_client.post(
            "/api/v1/auth/login",
            json={"email": password_user.email, "password": TEST_PASSWORD},
            headers=AUTH_ORIGIN_HEADERS,
        )
        assert second.status_code == 200
    application.dependency_overrides.clear()
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 2
