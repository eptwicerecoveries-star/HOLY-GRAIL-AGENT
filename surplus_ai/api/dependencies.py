"""Request-scoped SQLAlchemy session dependency (read-only P1)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from surplus_ai.database.engine import get_session_factory


def get_db_session() -> Iterator[Session]:
    """Yield a request-scoped session. Does not commit — read endpoints only."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()