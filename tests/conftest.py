from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import surplus_ai.database.models  # noqa: F401  (registers models on Base.metadata)
from surplus_ai.database.base import Base

TEST_DATABASE_URL = os.environ.get(
    "SURPLUS_AI_TEST_DATABASE_URL",
    "postgresql+psycopg://surplus_ai:surplus_ai_dev@localhost:5432/surplus_ai_test",
)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Session-scoped engine against the dedicated test database."""
    eng = create_engine(TEST_DATABASE_URL, future=True)
    try:
        with eng.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Test database unavailable at {TEST_DATABASE_URL}: {exc}")
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def _schema(engine: Engine) -> Iterator[None]:
    """Create the full schema once per test session, dropping it afterwards."""
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session(engine: Engine, _schema: None) -> Iterator[Session]:
    """Function-scoped session whose writes are always rolled back.

    `join_transaction_mode="create_savepoint"` keeps the outer transaction alive even when
    a test provokes an IntegrityError, so every test starts from the same clean schema.
    """
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
    db_session = factory()
    try:
        yield db_session
    finally:
        db_session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
