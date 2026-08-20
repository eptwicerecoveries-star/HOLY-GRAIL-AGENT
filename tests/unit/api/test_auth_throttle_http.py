"""P4-B2-B HTTP login throttle integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.api.app import create_app
from surplus_ai.api.dependencies import get_db_session, get_writable_db_session
from surplus_ai.auth.login_throttle import (
    CREDENTIAL_FAILURE_THRESHOLD,
    IP_ATTEMPT_THRESHOLD,
    LoginThrottleScope,
    digest_credential_key,
    digest_ip_key,
)
from surplus_ai.auth.passwords import hash_password
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.login_throttle_bucket import LoginThrottleBucket
from surplus_ai.database.models.user import User
from surplus_ai.utils.config import DEFAULT_DEV_AUTH_ORIGIN

TEST_PASSWORD = "local-test-password!"
AUTH_ORIGIN = {"Origin": DEFAULT_DEV_AUTH_ORIGIN}

_THROTTLED_BODY = {
    "code": "login_throttled",
    "message": "Too many login attempts. Try again later.",
}

_FAILURE_BODY = {
    "code": "authentication_failed",
    "message": "Invalid email or password.",
}

TEST_IP_A = "192.0.2.10"
TEST_IP_B = "192.0.2.11"


def _build_client(
    session: Session, *, client_ip: str = TEST_IP_A
) -> tuple[object, TestClient]:
    application = create_app()

    def _override() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db_session] = _override
    application.dependency_overrides[get_writable_db_session] = _override
    client = TestClient(
        application,
        raise_server_exceptions=False,
        client=(client_ip, 50000),
    )
    client.__enter__()
    return application, client


def _login(
    client: TestClient,
    *,
    email: str = "operator@example.invalid",
    password: str = TEST_PASSWORD,
) -> object:
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers=AUTH_ORIGIN,
    )


def _ip_bucket(session: Session, ip: str) -> LoginThrottleBucket | None:
    return session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.IP.value,
            LoginThrottleBucket.key_digest == digest_ip_key(ip),
        )
    )


def _cred_bucket(
    session: Session, ip: str, email: str
) -> LoginThrottleBucket | None:
    return session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.CREDENTIAL.value,
            LoginThrottleBucket.key_digest == digest_credential_key(ip, email),
        )
    )


# ── 429 envelope & Retry-After (spec §44) ────────────────────────────


def test_429_envelope_and_retry_after(
    session: Session, password_user: User
) -> None:
    app, client = _build_client(session)
    try:
        for _ in range(IP_ATTEMPT_THRESHOLD):
            _login(client, email="wrong@example.invalid", password="x")
        resp = _login(client, email="another@example.invalid", password="x")
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 429
    assert resp.json() == _THROTTLED_BODY
    ra = resp.headers.get("Retry-After")
    assert ra is not None
    assert int(ra) >= 1


# ── Credential 5/6 test (spec §29) ──────────────────────────────────


def test_credential_5_then_6_throttle(
    session: Session, password_user: User
) -> None:
    email = password_user.email
    app, client = _build_client(session)
    try:
        for i in range(CREDENTIAL_FAILURE_THRESHOLD):
            resp = _login(client, email=email, password="wrong!")
            assert resp.status_code == 401, f"attempt {i + 1}"
            assert resp.json() == _FAILURE_BODY

        resp6 = _login(client, email=email, password="wrong!")
        assert resp6.status_code == 429
        assert resp6.json() == _THROTTLED_BODY
        assert int(resp6.headers["Retry-After"]) >= 1
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    for r in [resp, resp6]:
        cookie = r.headers.get("set-cookie") or ""
        assert "surplus_ai_session" not in cookie


# ── Email variation 20/21 (spec §30) ─────────────────────────────────


def test_email_variation_20_21_ip_gate(session: Session) -> None:
    app, client = _build_client(session)
    calls: list[str] = []

    def _spy_verify(user: object, password: str) -> bool:
        calls.append("verify")
        from surplus_ai.auth.user_admin import verify_user_credentials as real
        return real(user, password)

    def _spy_dummy(password: str) -> None:
        calls.append("dummy")
        from surplus_ai.api.auth_timing import run_unknown_user_password_check as real
        real(password)

    try:
        for i in range(IP_ATTEMPT_THRESHOLD):
            email = f"user{i}@example.invalid"
            resp = _login(client, email=email, password="x")
            assert resp.status_code == 401, f"attempt {i + 1}"

        calls.clear()
        with (
            patch(
                "surplus_ai.api.routes.auth.verify_user_credentials", _spy_verify
            ),
            patch(
                "surplus_ai.api.routes.auth.run_unknown_user_password_check",
                _spy_dummy,
            ),
        ):
            resp21 = _login(
                client, email="fresh21@example.invalid", password="x"
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp21.status_code == 429
    assert resp21.json() == _THROTTLED_BODY
    assert calls == [], "Argon2 must not run when IP is blocked"


# ── Blocked IP skips expensive work (spec §31) ───────────────────────


def test_blocked_ip_skips_argon2_and_user_lookup(
    session: Session, password_user: User
) -> None:
    app, client = _build_client(session)
    calls: list[str] = []

    def _spy_verify(user: object, password: str) -> bool:
        calls.append("verify")
        return False

    def _spy_dummy(password: str) -> None:
        calls.append("dummy")

    try:
        for _ in range(IP_ATTEMPT_THRESHOLD):
            _login(client, email=f"u{_}@x.invalid", password="x")

        with (
            patch(
                "surplus_ai.api.routes.auth.verify_user_credentials", _spy_verify
            ),
            patch(
                "surplus_ai.api.routes.auth.run_unknown_user_password_check",
                _spy_dummy,
            ),
        ):
            resp = _login(
                client,
                email=password_user.email,
                password=TEST_PASSWORD,
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 429
    assert calls == []
    cookie = resp.headers.get("set-cookie") or ""
    assert "surplus_ai_session" not in cookie


# ── Blocked credential skips expensive work (spec §32) ───────────────


def test_blocked_credential_skips_argon2(
    session: Session, password_user: User
) -> None:
    email = password_user.email
    app, client = _build_client(session)
    calls: list[str] = []

    def _spy_verify(user: object, password: str) -> bool:
        calls.append("verify")
        return False

    def _spy_dummy(password: str) -> None:
        calls.append("dummy")

    try:
        for _ in range(CREDENTIAL_FAILURE_THRESHOLD):
            _login(client, email=email, password="wrong!")

        calls.clear()
        with (
            patch(
                "surplus_ai.api.routes.auth.verify_user_credentials", _spy_verify
            ),
            patch(
                "surplus_ai.api.routes.auth.run_unknown_user_password_check",
                _spy_dummy,
            ),
        ):
            resp = _login(client, email=email, password="wrong!")
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 429
    assert calls == []


# ── Different-IP isolation (spec §33) ────────────────────────────────


def test_different_ip_credential_isolation(
    session: Session, password_user: User
) -> None:
    email = password_user.email

    app_a, client_a = _build_client(session, client_ip=TEST_IP_A)
    app_b, client_b = _build_client(session, client_ip=TEST_IP_B)
    try:
        for _ in range(CREDENTIAL_FAILURE_THRESHOLD):
            resp = _login(client_a, email=email, password="wrong!")
            assert resp.status_code == 401

        blocked = _login(client_a, email=email, password="wrong!")
        assert blocked.status_code == 429

        ok = _login(client_b, email=email, password=TEST_PASSWORD)
        assert ok.status_code == 200
    finally:
        client_a.__exit__(None, None, None)
        client_b.__exit__(None, None, None)
        app_a.dependency_overrides.clear()
        app_b.dependency_overrides.clear()


# ── Origin rejection creates zero throttle state (spec §28) ──────────


def test_origin_rejection_no_throttle_state(
    session: Session, password_user: User
) -> None:
    app, client = _build_client(session)
    try:
        resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": password_user.email,
                "password": TEST_PASSWORD,
            },
            headers={"Origin": "http://evil.example"},
        )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert _ip_bucket(session, TEST_IP_A) is None
    assert (
        _cred_bucket(session, TEST_IP_A, password_user.email) is None
    )


# ── Unknown user counts (spec §34) ──────────────────────────────────


def test_unknown_user_throttle_counts(session: Session) -> None:
    email = "nobody@example.invalid"
    app, client = _build_client(session)
    try:
        resp = _login(client, email=email, password="x")
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 401
    assert _ip_bucket(session, TEST_IP_A) is not None
    assert _cred_bucket(session, TEST_IP_A, email) is not None


# ── Inactive and NULL hash (spec §35) ────────────────────────────────


@pytest.mark.parametrize(
    "email,setup",
    [
        ("inactive@example.invalid", "inactive"),
        ("nullhash@example.invalid", "null_hash"),
    ],
)
def test_inactive_and_null_hash_consume_throttle(
    session: Session, email: str, setup: str
) -> None:
    if setup == "inactive":
        session.add(
            User(
                name="Inactive",
                email=email,
                role=UserRole.AGENT,
                is_active=False,
                password_hash=hash_password(TEST_PASSWORD),
            )
        )
    elif setup == "null_hash":
        session.add(
            User(
                name="Null Hash",
                email=email,
                role=UserRole.AGENT,
                is_active=True,
                password_hash=None,
            )
        )
    session.flush()

    app, client = _build_client(session)
    try:
        resp = _login(client, email=email, password=TEST_PASSWORD)
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 401
    assert resp.json() == _FAILURE_BODY
    assert _ip_bucket(session, TEST_IP_A) is not None
    assert _cred_bucket(session, TEST_IP_A, email) is not None


# ── Success does NOT reset IP bucket (spec §36) ─────────────────────


def test_success_does_not_reset_ip_bucket(
    session: Session, password_user: User
) -> None:
    email = password_user.email
    app, client = _build_client(session)
    try:
        for _ in range(3):
            _login(client, email="wrong@x.invalid", password="x")

        ip_before = _ip_bucket(session, TEST_IP_A)
        assert ip_before is not None
        count_before = ip_before.event_count

        resp = _login(client, email=email, password=TEST_PASSWORD)
        assert resp.status_code == 200
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    session.expire_all()
    ip_after = _ip_bucket(session, TEST_IP_A)
    assert ip_after is not None
    assert ip_after.event_count == count_before + 1


# ── Success resets credential bucket (spec §37) ─────────────────────


def test_success_resets_credential_bucket(
    session: Session, password_user: User
) -> None:
    email = password_user.email
    app, client = _build_client(session)
    try:
        for _ in range(2):
            _login(client, email=email, password="wrong!")

        assert _cred_bucket(session, TEST_IP_A, email) is not None

        resp = _login(client, email=email, password=TEST_PASSWORD)
        assert resp.status_code == 200
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    session.expire_all()
    assert _cred_bucket(session, TEST_IP_A, email) is None
    assert _ip_bucket(session, TEST_IP_A) is not None


# ── 401 committed-state regression (spec §38) ───────────────────────


def test_401_committed_state_persists(
    session: Session, password_user: User
) -> None:
    email = password_user.email
    app, client = _build_client(session)
    try:
        resp = _login(client, email=email, password="wrong!")
        assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert _ip_bucket(session, TEST_IP_A) is not None
    assert _cred_bucket(session, TEST_IP_A, email) is not None


# ── Call order / COMMIT #1 before Argon2 (spec §39) ─────────────────


def test_commit_1_before_argon2(
    session: Session, password_user: User
) -> None:
    call_order: list[str] = []
    real_commit = session.commit

    def _spy_commit() -> None:
        call_order.append("commit")
        real_commit()

    def _spy_verify(user: object, password: str) -> bool:
        call_order.append("verify")
        from surplus_ai.auth.user_admin import verify_user_credentials as real
        return real(user, password)

    def _spy_dummy(password: str) -> None:
        call_order.append("dummy")
        from surplus_ai.api.auth_timing import run_unknown_user_password_check as real
        real(password)

    app, client = _build_client(session)
    try:
        with (
            patch.object(session, "commit", _spy_commit),
            patch(
                "surplus_ai.api.routes.auth.verify_user_credentials", _spy_verify
            ),
            patch(
                "surplus_ai.api.routes.auth.run_unknown_user_password_check",
                _spy_dummy,
            ),
        ):
            resp = _login(
                client,
                email=password_user.email,
                password=TEST_PASSWORD,
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    commit_idx = call_order.index("commit")
    verify_idx = call_order.index("verify")
    assert commit_idx < verify_idx, (
        f"COMMIT #1 at {commit_idx} must precede verify at {verify_idx}: {call_order}"
    )


def test_commit_1_before_dummy_argon2(session: Session) -> None:
    call_order: list[str] = []
    real_commit = session.commit

    def _spy_commit() -> None:
        call_order.append("commit")
        real_commit()

    def _spy_dummy(password: str) -> None:
        call_order.append("dummy")
        from surplus_ai.api.auth_timing import run_unknown_user_password_check as real
        real(password)

    app, client = _build_client(session)
    try:
        with (
            patch.object(session, "commit", _spy_commit),
            patch(
                "surplus_ai.api.routes.auth.run_unknown_user_password_check",
                _spy_dummy,
            ),
        ):
            resp = _login(
                client,
                email="nobody@example.invalid",
                password="x",
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 401
    commit_idx = call_order.index("commit")
    dummy_idx = call_order.index("dummy")
    assert commit_idx < dummy_idx


# ── Pre-Argon2 DB failure (spec §41) ────────────────────────────────


def test_ip_consume_failure_returns_500_no_argon2(
    session: Session, password_user: User
) -> None:
    calls: list[str] = []

    def _spy_verify(user: object, password: str) -> bool:
        calls.append("verify")
        return False

    def _spy_dummy(password: str) -> None:
        calls.append("dummy")

    app, client = _build_client(session)
    try:
        with (
            patch(
                "surplus_ai.api.routes.auth.consume_ip_login_attempt",
                side_effect=RuntimeError("simulated DB failure"),
            ),
            patch(
                "surplus_ai.api.routes.auth.verify_user_credentials", _spy_verify
            ),
            patch(
                "surplus_ai.api.routes.auth.run_unknown_user_password_check",
                _spy_dummy,
            ),
        ):
            resp = _login(
                client,
                email=password_user.email,
                password=TEST_PASSWORD,
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"
    assert calls == []
    cookie = resp.headers.get("set-cookie") or ""
    assert "surplus_ai_session" not in cookie


def test_commit_1_failure_returns_500_no_argon2(
    session: Session, password_user: User
) -> None:
    calls: list[str] = []
    commit_count = [0]
    real_commit = session.commit

    def _fail_first_commit() -> None:
        commit_count[0] += 1
        if commit_count[0] == 1:
            raise RuntimeError("simulated COMMIT #1 failure")
        real_commit()

    def _spy_verify(user: object, password: str) -> bool:
        calls.append("verify")
        return False

    def _spy_dummy(password: str) -> None:
        calls.append("dummy")

    app, client = _build_client(session)
    try:
        with (
            patch.object(session, "commit", _fail_first_commit),
            patch(
                "surplus_ai.api.routes.auth.verify_user_credentials", _spy_verify
            ),
            patch(
                "surplus_ai.api.routes.auth.run_unknown_user_password_check",
                _spy_dummy,
            ),
        ):
            resp = _login(
                client,
                email=password_user.email,
                password=TEST_PASSWORD,
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 500
    assert calls == []
    cookie = resp.headers.get("set-cookie") or ""
    assert "surplus_ai_session" not in cookie


# ── Post-Argon2 failure-persistence error (spec §42) ────────────────


def test_credential_failure_recording_error_returns_500(
    session: Session, password_user: User
) -> None:
    app, client = _build_client(session)
    try:
        with patch(
            "surplus_ai.api.routes.auth.record_credential_failure",
            side_effect=RuntimeError("simulated record failure"),
        ):
            resp = _login(
                client,
                email=password_user.email,
                password="wrong!",
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"
    cookie = resp.headers.get("set-cookie") or ""
    assert "surplus_ai_session" not in cookie


# ── Success commit failure (spec §43) ────────────────────────────────


def test_success_commit_failure_returns_500_no_cookie(
    session: Session, password_user: User
) -> None:
    commit_count = [0]
    real_commit = session.commit

    def _fail_second_commit() -> None:
        commit_count[0] += 1
        if commit_count[0] == 2:
            raise RuntimeError("simulated COMMIT #2B failure")
        real_commit()

    app, client = _build_client(session)
    try:
        with patch.object(session, "commit", _fail_second_commit):
            resp = _login(
                client,
                email=password_user.email,
                password=TEST_PASSWORD,
            )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"
    cookie = resp.headers.get("set-cookie") or ""
    assert "surplus_ai_session" not in cookie


# ── Cookie failure-path safety (spec §45) ────────────────────────────


@pytest.mark.parametrize(
    "scenario",
    ["wrong_password", "missing_origin", "unknown_email"],
)
def test_no_session_cookie_on_failure(
    session: Session, password_user: User, scenario: str
) -> None:
    app, client = _build_client(session)
    try:
        if scenario == "wrong_password":
            resp = _login(
                client,
                email=password_user.email,
                password="wrong!",
            )
            assert resp.status_code == 401
        elif scenario == "missing_origin":
            resp = client.post(
                "/api/v1/auth/login",
                json={
                    "email": password_user.email,
                    "password": TEST_PASSWORD,
                },
            )
            assert resp.status_code == 403
        elif scenario == "unknown_email":
            resp = _login(
                client, email="nobody@example.invalid", password="x"
            )
            assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    cookie = resp.headers.get("set-cookie") or ""
    assert "surplus_ai_session" not in cookie


# ── Credential 429 already consumed IP attempt (spec §13) ───────────


def test_credential_429_already_consumed_ip(
    session: Session, password_user: User
) -> None:
    email = password_user.email
    app, client = _build_client(session)
    try:
        for _ in range(CREDENTIAL_FAILURE_THRESHOLD):
            _login(client, email=email, password="wrong!")

        ip_before = _ip_bucket(session, TEST_IP_A)
        count_before = ip_before.event_count if ip_before else 0

        resp = _login(client, email=email, password="wrong!")
        assert resp.status_code == 429
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()

    session.expire_all()
    ip_after = _ip_bucket(session, TEST_IP_A)
    assert ip_after is not None
    assert ip_after.event_count == count_before + 1


# ── password_user fixture for tests that need it ─────────────────────


@pytest.fixture
def password_user(session: Session) -> User:
    user = User(
        name="API Operator",
        email="operator@example.invalid",
        role=UserRole.AGENT,
        is_active=True,
        password_hash=hash_password(TEST_PASSWORD),
    )
    session.add(user)
    session.flush()
    return user
