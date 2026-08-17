"""Server-side auth-session tokens (P3-B1).

Opaque random bearer tokens are never persisted. PostgreSQL stores SHA-256 hex
digests only. No HTTP, cookies, or FastAPI types.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.auth.exceptions import AuthSessionError
from surplus_ai.database.models.auth_session import AuthSession
from surplus_ai.database.models.user import User

SESSION_LIFETIME = timedelta(hours=12)
_MAX_RAW_TOKEN_LENGTH = 256

WallClockFn = Callable[[], datetime]


@dataclass(frozen=True)
class CreatedAuthSession:
    """Ephemeral create result. ``raw_token`` is secret and omitted from repr/str."""

    auth_session_id: uuid.UUID
    user_id: uuid.UUID
    expires_at: datetime
    raw_token: str = field(repr=False)


@dataclass(frozen=True)
class ResolvedAuthSession:
    auth_session_id: uuid.UUID
    user_id: uuid.UUID
    user: User


def _now(wall_clock: WallClockFn | None) -> datetime:
    current = wall_clock() if wall_clock is not None else datetime.now(tz=UTC)
    if current.tzinfo is None:
        raise AuthSessionError("Session clock must be timezone-aware UTC.")
    return current


def generate_session_token() -> str:
    """Return a URL-safe token with 32 random bytes (256 bits) of entropy."""
    return secrets.token_urlsafe(32)


def digest_session_token(raw_token: str) -> str:
    """Return the 64-character lowercase SHA-256 hex digest of ``raw_token``."""
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def _normalized_token(raw_token: object) -> str | None:
    if not isinstance(raw_token, str) or raw_token == "":
        return None
    if len(raw_token) > _MAX_RAW_TOKEN_LENGTH:
        return None
    try:
        raw_token.encode("ascii")
    except UnicodeEncodeError:
        return None
    return raw_token


def create_auth_session(
    session: Session,
    *,
    user_id: uuid.UUID,
    wall_clock: WallClockFn | None = None,
) -> CreatedAuthSession:
    """Persist a digest-only session for an active User. Does not commit."""
    user = session.get(User, user_id)
    if user is None:
        raise AuthSessionError("No user found.")
    if not user.is_active:
        raise AuthSessionError("User is not active.")
    now = _now(wall_clock)
    raw_token = generate_session_token()
    expires_at = now + SESSION_LIFETIME
    row = AuthSession(
        user_id=user.id,
        token_digest=digest_session_token(raw_token),
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return CreatedAuthSession(
        auth_session_id=row.id,
        user_id=user.id,
        expires_at=row.expires_at,
        raw_token=raw_token,
    )


def resolve_auth_session(
    session: Session,
    *,
    raw_token: object,
    wall_clock: WallClockFn | None = None,
) -> ResolvedAuthSession | None:
    """Return the active User for a valid unexpired token. Read-only. Does not commit."""
    token = _normalized_token(raw_token)
    if token is None:
        return None
    digest = digest_session_token(token)
    row = session.scalar(select(AuthSession).where(AuthSession.token_digest == digest))
    if row is None:
        return None
    now = _now(wall_clock)
    if row.expires_at <= now:
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return ResolvedAuthSession(auth_session_id=row.id, user_id=user.id, user=user)


def revoke_auth_session(session: Session, *, raw_token: object) -> bool:
    """Delete the exact session row for ``raw_token``. Idempotent. Does not commit."""
    token = _normalized_token(raw_token)
    if token is None:
        return False
    digest = digest_session_token(token)
    row = session.scalar(select(AuthSession).where(AuthSession.token_digest == digest))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
