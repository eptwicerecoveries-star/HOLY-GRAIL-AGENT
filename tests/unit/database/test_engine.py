from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from surplus_ai.database.engine import DatabaseError, session_scope
from surplus_ai.utils.exceptions import AppError


def test_database_error_is_an_app_error() -> None:
    assert issubclass(DatabaseError, AppError)


def test_session_scope_commits_on_success(monkeypatch: pytest.MonkeyPatch, engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.orm import sessionmaker

    import surplus_ai.database.engine as engine_module

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_module, "get_session_factory", lambda: factory)

    with session_scope() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_session_scope_wraps_sqlalchemy_errors(monkeypatch: pytest.MonkeyPatch, engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.orm import sessionmaker

    import surplus_ai.database.engine as engine_module

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_module, "get_session_factory", lambda: factory)

    with pytest.raises(DatabaseError):
        with session_scope() as session:
            session.execute(text("SELECT * FROM table_that_does_not_exist"))


def test_session_scope_reraises_non_sqlalchemy_errors(
    monkeypatch: pytest.MonkeyPatch,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    from sqlalchemy.orm import sessionmaker

    import surplus_ai.database.engine as engine_module

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_module, "get_session_factory", lambda: factory)

    with pytest.raises(ValueError, match="caller bug"):
        with session_scope():
            raise ValueError("caller bug")


def test_sqlalchemy_error_is_not_leaked(monkeypatch: pytest.MonkeyPatch, engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.orm import sessionmaker

    import surplus_ai.database.engine as engine_module

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_module, "get_session_factory", lambda: factory)

    try:
        with session_scope() as session:
            session.execute(text("SELECT * FROM nope"))
    except DatabaseError as exc:
        assert isinstance(exc.__cause__, SQLAlchemyError)
    else:  # pragma: no cover - guard
        pytest.fail("DatabaseError was not raised")
