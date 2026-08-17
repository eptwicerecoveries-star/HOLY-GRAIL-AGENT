"""P3-B1 auth-session token/digest service tests. Fake tokens only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from surplus_ai.auth.exceptions import AuthSessionError
from surplus_ai.auth.sessions import (
    SESSION_LIFETIME,
    CreatedAuthSession,
    create_auth_session,
    digest_session_token,
    generate_session_token,
    resolve_auth_session,
    revoke_auth_session,
)
from surplus_ai.database.models.auth_session import AuthSession
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.user import User

_FIXED = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


def _user(session: Session, *, email: str = "sess@example.invalid", active: bool = True) -> User:
    user = User(
        name="Session User",
        email=email,
        role=UserRole.AGENT,
        is_active=active,
    )
    session.add(user)
    session.flush()
    return user


def test_generate_session_token_uses_token_urlsafe_32(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def _fake(nbytes: int) -> str:
        calls.append(nbytes)
        return "deterministic-session-token-value"

    monkeypatch.setattr("surplus_ai.auth.sessions.secrets.token_urlsafe", _fake)
    assert generate_session_token() == "deterministic-session-token-value"
    assert calls == [32]


def test_generate_session_token_is_nonempty_and_distinct() -> None:
    first = generate_session_token()
    second = generate_session_token()
    assert isinstance(first, str) and first
    assert isinstance(second, str) and second
    assert first != second


def test_digest_is_deterministic_hex_and_not_raw_token() -> None:
    token = "opaque-session-token-sample"
    digest = digest_session_token(token)
    assert digest == digest_session_token(token)
    assert digest != token
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert digest_session_token("other-opaque-token-value") != digest


def test_created_auth_session_hides_raw_token_from_repr() -> None:
    token = "secret-raw-session-token"
    result = CreatedAuthSession(
        auth_session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        expires_at=_FIXED + SESSION_LIFETIME,
        raw_token=token,
    )
    assert token not in repr(result)
    assert token not in str(result)


def test_create_auth_session_for_active_user(session: Session) -> None:
    user = _user(session)
    created = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    assert created.user_id == user.id
    assert created.expires_at == _FIXED + SESSION_LIFETIME
    assert created.raw_token
    row = session.get(AuthSession, created.auth_session_id)
    assert row is not None
    assert row.user_id == user.id
    assert row.token_digest == digest_session_token(created.raw_token)
    assert created.raw_token not in row.token_digest
    assert "raw_token" not in AuthSession.__table__.c.keys()
    assert "token" not in AuthSession.__table__.c.keys()
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_create_rejects_inactive_and_missing_user(session: Session) -> None:
    inactive = _user(session, email="inactive@example.invalid", active=False)
    with pytest.raises(AuthSessionError, match="not active"):
        create_auth_session(session, user_id=inactive.id)
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 0

    with pytest.raises(AuthSessionError, match="No user found"):
        create_auth_session(session, user_id=uuid.uuid4())
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_resolve_valid_session_is_read_only(session: Session) -> None:
    user = _user(session)
    created = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    expires_before = session.get(AuthSession, created.auth_session_id)
    assert expires_before is not None
    original_expires = expires_before.expires_at
    resolved = resolve_auth_session(
        session, raw_token=created.raw_token, wall_clock=lambda: _FIXED
    )
    assert resolved is not None
    assert resolved.user_id == user.id
    assert resolved.auth_session_id == created.auth_session_id
    assert resolved.user.email == user.email
    row = session.get(AuthSession, created.auth_session_id)
    assert row is not None
    assert row.expires_at == original_expires


def test_resolve_expiry_boundary(session: Session) -> None:
    user = _user(session)
    created = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    just_before = created.expires_at - timedelta(microseconds=1)
    assert (
        resolve_auth_session(session, raw_token=created.raw_token, wall_clock=lambda: just_before)
        is not None
    )
    assert (
        resolve_auth_session(
            session, raw_token=created.raw_token, wall_clock=lambda: created.expires_at
        )
        is None
    )
    after = created.expires_at + timedelta(seconds=1)
    assert (
        resolve_auth_session(session, raw_token=created.raw_token, wall_clock=lambda: after) is None
    )
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_resolve_invalid_tokens(session: Session) -> None:
    assert resolve_auth_session(session, raw_token="") is None
    assert resolve_auth_session(session, raw_token=None) is None
    assert resolve_auth_session(session, raw_token=123) is None
    assert resolve_auth_session(session, raw_token="unknown-random-token-value") is None
    assert resolve_auth_session(session, raw_token="café-non-ascii") is None
    assert resolve_auth_session(session, raw_token="x" * 257) is None
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_inactive_user_after_session_creation(session: Session) -> None:
    user = _user(session)
    created = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    user.is_active = False
    session.flush()
    resolved = resolve_auth_session(
        session, raw_token=created.raw_token, wall_clock=lambda: _FIXED
    )
    assert resolved is None
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_user_delete_cascades_auth_sessions(session: Session) -> None:
    user = _user(session)
    created = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    session.delete(user)
    session.flush()
    assert session.get(AuthSession, created.auth_session_id) is None
    assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_revoke_is_idempotent(session: Session) -> None:
    user = _user(session)
    created = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    assert resolve_auth_session(
        session, raw_token=created.raw_token, wall_clock=lambda: _FIXED
    )
    assert revoke_auth_session(session, raw_token=created.raw_token) is True
    assert (
        resolve_auth_session(
            session, raw_token=created.raw_token, wall_clock=lambda: _FIXED
        )
        is None
    )
    assert revoke_auth_session(session, raw_token=created.raw_token) is False
    assert revoke_auth_session(session, raw_token="") is False
    assert revoke_auth_session(session, raw_token=None) is False


def test_multiple_sessions_are_independent(session: Session) -> None:
    user = _user(session)
    first = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    second = create_auth_session(session, user_id=user.id, wall_clock=lambda: _FIXED)
    assert first.auth_session_id != second.auth_session_id
    assert first.raw_token != second.raw_token
    assert digest_session_token(first.raw_token) != digest_session_token(second.raw_token)
    assert resolve_auth_session(
        session, raw_token=first.raw_token, wall_clock=lambda: _FIXED
    )
    assert resolve_auth_session(
        session, raw_token=second.raw_token, wall_clock=lambda: _FIXED
    )
    assert revoke_auth_session(session, raw_token=first.raw_token) is True
    assert (
        resolve_auth_session(
            session, raw_token=first.raw_token, wall_clock=lambda: _FIXED
        )
        is None
    )
    assert resolve_auth_session(
        session, raw_token=second.raw_token, wall_clock=lambda: _FIXED
    )


def test_token_digest_unique_constraint(session: Session) -> None:
    first_user = _user(session, email="one@example.invalid")
    second_user = _user(session, email="two@example.invalid")
    digest = digest_session_token("shared-digest-collision-fixture")
    session.add(
        AuthSession(
            user_id=first_user.id,
            token_digest=digest,
            expires_at=_FIXED + SESSION_LIFETIME,
        )
    )
    session.flush()
    session.add(
        AuthSession(
            user_id=second_user.id,
            token_digest=digest,
            expires_at=_FIXED + SESSION_LIFETIME,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
