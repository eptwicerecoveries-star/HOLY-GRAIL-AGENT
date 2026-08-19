"""P4-B2-A login throttle service tests. Fake example.invalid only."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

import surplus_ai.database.models  # noqa: F401
from surplus_ai.auth.login_throttle import (
    CREDENTIAL_BLOCK_DURATION,
    CREDENTIAL_FAILURE_THRESHOLD,
    IP_ATTEMPT_THRESHOLD,
    IP_BLOCK_DURATION,
    LoginThrottleScope,
    ThrottleDecision,
    canonical_client_ip,
    clear_credential_bucket_on_success,
    consume_ip_login_attempt,
    digest_credential_key,
    digest_ip_key,
    precheck_credential_throttle,
    record_credential_failure,
    retry_after_seconds,
)
from surplus_ai.database.base import Base
from surplus_ai.database.models.login_throttle_bucket import LoginThrottleBucket
from tests.conftest import TEST_DATABASE_URL

_FIXED = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
_CANONICAL_IP = "192.0.2.1"
_EMAIL = "operator@example.invalid"


def test_canonical_ipv4() -> None:
    assert canonical_client_ip("192.0.2.1") == "192.0.2.1"


def test_ipv4_mapped_collapses_to_ipv4() -> None:
    assert canonical_client_ip("::ffff:192.0.2.1") == "192.0.2.1"
    assert digest_ip_key(canonical_client_ip("192.0.2.1")) == digest_ip_key(
        canonical_client_ip("::ffff:192.0.2.1")
    )


def test_canonical_pure_ipv6() -> None:
    assert canonical_client_ip("2001:db8::1") == "2001:db8::1"


def test_unknown_client_sentinel() -> None:
    assert canonical_client_ip(None) == "unknown-client"
    assert canonical_client_ip("") == "unknown-client"
    assert canonical_client_ip("   ") == "unknown-client"
    assert canonical_client_ip("not-an-ip") == "unknown-client"


def test_digests_are_deterministic_lowercase_hex() -> None:
    ip_digest = digest_ip_key(_CANONICAL_IP)
    cred_digest = digest_credential_key(_CANONICAL_IP, _EMAIL)
    assert ip_digest == digest_ip_key(_CANONICAL_IP)
    assert cred_digest == digest_credential_key(_CANONICAL_IP, _EMAIL)
    assert len(ip_digest) == 64
    assert ip_digest == ip_digest.lower()
    assert cred_digest != digest_credential_key(_CANONICAL_IP, "other@example.invalid")
    assert cred_digest != digest_credential_key("203.0.113.9", _EMAIL)


def test_email_case_affects_credential_digest() -> None:
    lower = digest_credential_key(_CANONICAL_IP, "User@Example.invalid")
    upper = digest_credential_key(_CANONICAL_IP, "user@example.invalid")
    assert lower != upper


def test_throttle_decision_is_frozen_minimal() -> None:
    decision = ThrottleDecision(blocked=True, retry_after_seconds=30)
    with pytest.raises(AttributeError):
        decision.blocked = False  # type: ignore[misc]
    assert decision.retry_after_seconds == 30


def test_retry_after_never_zero() -> None:
    now = _FIXED
    blocked_until = now + timedelta(milliseconds=100)
    assert retry_after_seconds(blocked_until=blocked_until, now=now) >= 1


def test_retry_after_uses_ceil() -> None:
    now = _FIXED
    blocked_until = now + timedelta(seconds=1, milliseconds=1)
    assert retry_after_seconds(blocked_until=blocked_until, now=now) == 2


def test_privacy_model_has_no_raw_identifier_columns() -> None:
    forbidden = {
        "ip",
        "ip_address",
        "email",
        "user_id",
        "user_agent",
        "password",
        "password_hash",
        "token",
        "session_token",
        "session_digest",
    }
    column_names = {c.key for c in LoginThrottleBucket.__table__.columns}
    assert forbidden.isdisjoint(column_names)
    assert column_names == {
        "scope",
        "key_digest",
        "window_started_at",
        "event_count",
        "blocked_until",
    }


@pytest.fixture
def throttle_session() -> Session:
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def _clock_at(fixed: datetime):
    return lambda: fixed


def _ip_row(session: Session, canonical_ip: str = _CANONICAL_IP) -> LoginThrottleBucket:
    row = session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.IP.value,
            LoginThrottleBucket.key_digest == digest_ip_key(canonical_ip),
        )
    )
    assert row is not None
    return row


def test_ip_consume_new_row_allowed(throttle_session: Session) -> None:
    decision = consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
    )
    assert decision == ThrottleDecision(blocked=False, retry_after_seconds=None)
    row = _ip_row(throttle_session)
    assert row.event_count == 1
    assert row.blocked_until is None


def test_ip_consume_attempts_1_through_19_allowed(throttle_session: Session) -> None:
    for _ in range(1, IP_ATTEMPT_THRESHOLD):
        decision = consume_ip_login_attempt(
            throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
        )
        assert decision.blocked is False
        throttle_session.commit()
    row = _ip_row(throttle_session)
    assert row.event_count == IP_ATTEMPT_THRESHOLD - 1
    assert row.blocked_until is None


def test_ip_consume_attempt_20_allowed_and_establishes_block(throttle_session: Session) -> None:
    for _ in range(IP_ATTEMPT_THRESHOLD - 1):
        consume_ip_login_attempt(
            throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
        )
        throttle_session.commit()
    decision = consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
    )
    assert decision.blocked is False
    throttle_session.commit()
    row = _ip_row(throttle_session)
    assert row.event_count == IP_ATTEMPT_THRESHOLD
    assert row.blocked_until == _FIXED + IP_BLOCK_DURATION


def test_ip_consume_attempt_21_blocked_without_increment(throttle_session: Session) -> None:
    for _ in range(IP_ATTEMPT_THRESHOLD):
        consume_ip_login_attempt(
            throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
        )
        throttle_session.commit()
    blocked_at = _FIXED + IP_BLOCK_DURATION
    decision = consume_ip_login_attempt(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        wall_clock=_clock_at(_FIXED + timedelta(seconds=1)),
    )
    assert decision.blocked is True
    assert decision.retry_after_seconds is not None
    row = _ip_row(throttle_session)
    assert row.event_count == IP_ATTEMPT_THRESHOLD
    assert row.blocked_until == blocked_at


def test_ip_active_block_not_extended_by_blocked_requests(throttle_session: Session) -> None:
    for _ in range(IP_ATTEMPT_THRESHOLD):
        consume_ip_login_attempt(
            throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
        )
        throttle_session.commit()
    blocked_at = _FIXED + IP_BLOCK_DURATION
    later = _FIXED + timedelta(minutes=1)
    decision = consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(later)
    )
    assert decision.blocked is True
    row = _ip_row(throttle_session)
    assert row.blocked_until == blocked_at
    assert row.event_count == IP_ATTEMPT_THRESHOLD


def test_ip_window_expiry_resets_to_count_one(throttle_session: Session) -> None:
    consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
    )
    throttle_session.commit()
    expired_now = _FIXED + timedelta(minutes=16)
    decision = consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(expired_now)
    )
    assert decision.blocked is False
    row = _ip_row(throttle_session)
    assert row.event_count == 1
    assert row.window_started_at == expired_now
    assert row.blocked_until is None


def test_ip_block_expiry_resets_to_count_one(throttle_session: Session) -> None:
    for _ in range(IP_ATTEMPT_THRESHOLD):
        consume_ip_login_attempt(
            throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
        )
        throttle_session.commit()
    expired_now = _FIXED + IP_BLOCK_DURATION
    decision = consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(expired_now)
    )
    assert decision.blocked is False
    row = _ip_row(throttle_session)
    assert row.event_count == 1
    assert row.window_started_at == expired_now
    assert row.blocked_until is None


def test_ip_no_raw_ip_stored(throttle_session: Session) -> None:
    consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
    )
    throttle_session.commit()
    row = _ip_row(throttle_session)
    assert _CANONICAL_IP not in repr(row)
    assert row.key_digest == digest_ip_key(_CANONICAL_IP)


def test_ip_commit_visibility_two_sessions() -> None:
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session_a = factory()
    session_b = factory()
    try:
        consume_ip_login_attempt(
            session_a, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
        )
        session_b_row = session_b.scalar(select(LoginThrottleBucket))
        assert session_b_row is None
        session_a.commit()
        session_b.expire_all()
        row = session_b.scalar(select(LoginThrottleBucket))
        assert row is not None
        assert row.event_count == 1
    finally:
        session_a.close()
        session_b.close()
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def test_ip_concurrency_budget_exactly_twenty_allowed() -> None:
    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    allowed = 0
    blocked = 0
    try:
        for _ in range(IP_ATTEMPT_THRESHOLD + 3):
            session = factory()
            decision = consume_ip_login_attempt(
                session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
            )
            session.commit()
            session.close()
            if decision.blocked:
                blocked += 1
            else:
                allowed += 1
        assert allowed == IP_ATTEMPT_THRESHOLD
        assert blocked == 3
        verify = factory()
        row = verify.scalar(
            select(LoginThrottleBucket).where(
                LoginThrottleBucket.scope == LoginThrottleScope.IP.value
            )
        )
        assert row is not None
        assert row.event_count == IP_ATTEMPT_THRESHOLD
        verify.close()
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def test_ip_overlapping_transactions_serialize_at_threshold() -> None:
    """Two overlapping consumes of the same IP bucket must serialize in PostgreSQL.

    Session A consumes attempt 20 and holds the transaction. Session B's upsert
    must wait on that row lock, then observe the committed block.
    """
    overlap_ip = "192.0.2.81"
    key_digest = digest_ip_key(overlap_ip)
    engine = create_engine(TEST_DATABASE_URL, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    setup = factory()
    session_a = factory()
    b_finished = Event()
    b_pid_ready = Event()
    b_result: dict[str, object] = {}

    def _run_b() -> None:
        session_b = factory()
        try:
            pid = session_b.connection().execute(text("SELECT pg_backend_pid()")).scalar_one()
            b_result["pid"] = int(pid)
            b_pid_ready.set()
            b_result["decision"] = consume_ip_login_attempt(
                session_b, canonical_ip=overlap_ip, wall_clock=_clock_at(_FIXED)
            )
        finally:
            session_b.close()
            b_finished.set()

    try:
        setup.execute(
            text(
                "DELETE FROM login_throttle_buckets "
                "WHERE scope = :scope AND key_digest = :digest"
            ),
            {"scope": LoginThrottleScope.IP.value, "digest": key_digest},
        )
        setup.commit()
        for _ in range(IP_ATTEMPT_THRESHOLD - 1):
            decision = consume_ip_login_attempt(
                setup, canonical_ip=overlap_ip, wall_clock=_clock_at(_FIXED)
            )
            assert decision.blocked is False
            setup.commit()
        pre = setup.scalar(
            select(LoginThrottleBucket).where(
                LoginThrottleBucket.scope == LoginThrottleScope.IP.value,
                LoginThrottleBucket.key_digest == key_digest,
            )
        )
        assert pre is not None
        assert pre.event_count == IP_ATTEMPT_THRESHOLD - 1
        assert pre.blocked_until is None
        setup.close()

        decision_a = consume_ip_login_attempt(
            session_a, canonical_ip=overlap_ip, wall_clock=_clock_at(_FIXED)
        )
        assert decision_a.blocked is False
        session_a.flush()
        row_a = session_a.scalar(
            select(LoginThrottleBucket).where(
                LoginThrottleBucket.scope == LoginThrottleScope.IP.value,
                LoginThrottleBucket.key_digest == key_digest,
            )
        )
        assert row_a is not None
        assert row_a.event_count == IP_ATTEMPT_THRESHOLD
        blocked_until = row_a.blocked_until
        assert blocked_until == _FIXED + IP_BLOCK_DURATION

        worker = Thread(target=_run_b, name="ip-throttle-overlap-b", daemon=True)
        worker.start()
        assert b_pid_ready.wait(timeout=10)
        pid_b = b_result["pid"]
        assert isinstance(pid_b, int)

        saw_ungranted_lock = False
        deadline = time.monotonic() + 10.0
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
            lock_sql = text(
                "SELECT COUNT(*) FROM pg_locks WHERE pid = :pid AND NOT granted"
            )
            while time.monotonic() < deadline:
                waiting = observer.execute(lock_sql, {"pid": pid_b}).scalar_one()
                if int(waiting) > 0:
                    saw_ungranted_lock = True
                    break
        assert saw_ungranted_lock, "session B did not wait on session A's uncommitted row"
        assert not b_finished.is_set()

        session_a.commit()
        assert b_finished.wait(timeout=10)
        worker.join(timeout=10)
        decision_b = b_result["decision"]
        assert isinstance(decision_b, ThrottleDecision)
        assert decision_b.blocked is True
        assert decision_b.retry_after_seconds is not None

        verify = factory()
        rows = verify.scalars(
            select(LoginThrottleBucket).where(
                LoginThrottleBucket.scope == LoginThrottleScope.IP.value,
                LoginThrottleBucket.key_digest == key_digest,
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].event_count == IP_ATTEMPT_THRESHOLD
        assert rows[0].blocked_until == blocked_until
        verify.close()
    finally:
        session_a.close()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM login_throttle_buckets "
                    "WHERE scope = :scope AND key_digest = :digest"
                ),
                {"scope": LoginThrottleScope.IP.value, "digest": key_digest},
            )
        engine.dispose()


def test_credential_precheck_missing_allowed(throttle_session: Session) -> None:
    decision = precheck_credential_throttle(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED),
    )
    assert decision.blocked is False


def test_credential_failure_establishes_block_on_fifth(throttle_session: Session) -> None:
    for _ in range(CREDENTIAL_FAILURE_THRESHOLD):
        record_credential_failure(
            throttle_session,
            canonical_ip=_CANONICAL_IP,
            submitted_email=_EMAIL,
            wall_clock=_clock_at(_FIXED),
        )
        throttle_session.commit()
    decision = precheck_credential_throttle(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED + timedelta(seconds=1)),
    )
    assert decision.blocked is True
    row = throttle_session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.CREDENTIAL.value,
            LoginThrottleBucket.key_digest == digest_credential_key(_CANONICAL_IP, _EMAIL),
        )
    )
    assert row is not None
    assert row.event_count == CREDENTIAL_FAILURE_THRESHOLD
    assert row.blocked_until == _FIXED + CREDENTIAL_BLOCK_DURATION


def test_credential_active_block_not_extended_on_late_failure(
    throttle_session: Session,
) -> None:
    for _ in range(CREDENTIAL_FAILURE_THRESHOLD):
        record_credential_failure(
            throttle_session,
            canonical_ip=_CANONICAL_IP,
            submitted_email=_EMAIL,
            wall_clock=_clock_at(_FIXED),
        )
        throttle_session.commit()
    blocked_at = _FIXED + CREDENTIAL_BLOCK_DURATION
    record_credential_failure(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED + timedelta(minutes=1)),
    )
    throttle_session.commit()
    row = throttle_session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.CREDENTIAL.value,
            LoginThrottleBucket.key_digest == digest_credential_key(_CANONICAL_IP, _EMAIL),
        )
    )
    assert row is not None
    assert row.event_count == CREDENTIAL_FAILURE_THRESHOLD
    assert row.blocked_until == blocked_at


def test_credential_expired_block_resets_to_count_one(throttle_session: Session) -> None:
    for _ in range(CREDENTIAL_FAILURE_THRESHOLD):
        record_credential_failure(
            throttle_session,
            canonical_ip=_CANONICAL_IP,
            submitted_email=_EMAIL,
            wall_clock=_clock_at(_FIXED),
        )
        throttle_session.commit()
    expired_now = _FIXED + CREDENTIAL_BLOCK_DURATION
    record_credential_failure(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(expired_now),
    )
    throttle_session.commit()
    row = throttle_session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.CREDENTIAL.value,
            LoginThrottleBucket.key_digest == digest_credential_key(_CANONICAL_IP, _EMAIL),
        )
    )
    assert row is not None
    assert row.event_count == 1
    assert row.window_started_at == expired_now
    assert row.blocked_until is None


def test_credential_different_ip_isolated(throttle_session: Session) -> None:
    record_credential_failure(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED),
    )
    throttle_session.commit()
    other = precheck_credential_throttle(
        throttle_session,
        canonical_ip="203.0.113.9",
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED),
    )
    assert other.blocked is False


def test_credential_different_email_isolated(throttle_session: Session) -> None:
    record_credential_failure(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED),
    )
    throttle_session.commit()
    other = precheck_credential_throttle(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email="other@example.invalid",
        wall_clock=_clock_at(_FIXED),
    )
    assert other.blocked is False


def test_clear_credential_bucket_on_success_removes_row_only(throttle_session: Session) -> None:
    consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
    )
    record_credential_failure(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED),
    )
    throttle_session.commit()
    cleared = clear_credential_bucket_on_success(
        throttle_session, canonical_ip=_CANONICAL_IP, submitted_email=_EMAIL
    )
    assert cleared is True
    throttle_session.commit()
    cred = throttle_session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.CREDENTIAL.value
        )
    )
    ip_row = throttle_session.scalar(
        select(LoginThrottleBucket).where(LoginThrottleBucket.scope == LoginThrottleScope.IP.value)
    )
    assert cred is None
    assert ip_row is not None


def test_clear_credential_missing_is_idempotent(throttle_session: Session) -> None:
    assert (
        clear_credential_bucket_on_success(
            throttle_session, canonical_ip=_CANONICAL_IP, submitted_email=_EMAIL
        )
        is False
    )


def test_service_does_not_commit(
    throttle_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    commits: list[object] = []
    original = throttle_session.commit

    def _track_commit() -> None:
        commits.append(True)
        original()

    monkeypatch.setattr(throttle_session, "commit", _track_commit)
    rollbacks: list[object] = []
    original_rollback = throttle_session.rollback

    def _track_rollback() -> None:
        rollbacks.append(True)
        original_rollback()

    monkeypatch.setattr(throttle_session, "rollback", _track_rollback)
    consume_ip_login_attempt(
        throttle_session, canonical_ip=_CANONICAL_IP, wall_clock=_clock_at(_FIXED)
    )
    record_credential_failure(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED),
    )
    precheck_credential_throttle(
        throttle_session,
        canonical_ip=_CANONICAL_IP,
        submitted_email=_EMAIL,
        wall_clock=_clock_at(_FIXED),
    )
    clear_credential_bucket_on_success(
        throttle_session, canonical_ip=_CANONICAL_IP, submitted_email=_EMAIL
    )
    assert commits == []
    assert rollbacks == []


def test_orm_column_names_match_design() -> None:
    orm = {c.key for c in LoginThrottleBucket.__table__.columns}
    assert orm == {"scope", "key_digest", "window_started_at", "event_count", "blocked_until"}
