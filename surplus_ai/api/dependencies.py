"""Request-scoped SQLAlchemy session and authentication dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from surplus_ai.api.errors import authentication_required
from surplus_ai.api.session_cookie import SESSION_COOKIE_NAME
from surplus_ai.auth.sessions import resolve_auth_session
from surplus_ai.database.engine import get_session_factory
from surplus_ai.database.models.user import User


def get_db_session() -> Iterator[Session]:
    """Yield a request-scoped session. Does not commit — read endpoints only."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_writable_db_session() -> Iterator[Session]:
    """Yield a session for auth writes. Caller must commit; no auto-commit."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_active_user(
    request: Request,
    session: Session = Depends(get_db_session),
) -> User:
    """Resolve the HttpOnly session cookie to an active User. Read-only."""
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    resolved = resolve_auth_session(session, raw_token=raw_token)
    if resolved is None:
        raise authentication_required()
    return resolved.user