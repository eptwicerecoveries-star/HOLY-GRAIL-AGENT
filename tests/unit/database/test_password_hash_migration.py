from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text

from surplus_ai.database.base import Base
from surplus_ai.database.migrations.versions.fc21ee2dd624_p3a_user_password_hash import (
    down_revision,
    revision,
)
from surplus_ai.database.migrator import head_revision
from tests.conftest import TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PREV = "f3a8d12e90b1"
_COLUMN = "password_hash"


def test_p3a_migration_revises_single_current_head() -> None:
    assert down_revision == _PREV
    assert revision == "fc21ee2dd624"
    assert head_revision() == revision


@pytest.fixture
def cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["SURPLUS_AI_DATABASE_URL"] = TEST_DATABASE_URL
    env["SURPLUS_AI_ENV"] = "dev"
    env["SURPLUS_AI_LOG_LEVEL"] = "WARNING"
    return env


@pytest.fixture
def migration_db(engine: Engine) -> Iterator[Engine]:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)


def _alembic(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _user_columns(engine: Engine) -> dict[str, bool]:
    return {col["name"]: col["nullable"] for col in inspect(engine).get_columns("users")}


def test_p3a_password_hash_migration_lifecycle(
    migration_db: Engine, cli_env: dict[str, str]
) -> None:
    up_prev = _alembic(["upgrade", _PREV], cli_env)
    assert up_prev.returncode == 0, up_prev.stderr
    assert _COLUMN not in _user_columns(migration_db)

    user_id = uuid.uuid4()
    with migration_db.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO users (id, name, email, role, is_active)
                VALUES (:id, 'Mig User', 'mig@example.invalid', 'agent', true)
                """
            ),
            {"id": user_id},
        )

    up_head = _alembic(["upgrade", "head"], cli_env)
    assert up_head.returncode == 0, up_head.stderr
    columns = _user_columns(migration_db)
    assert columns[_COLUMN] is True

    with migration_db.connect() as conn:
        row = conn.execute(
            text("SELECT name, email, role, is_active, password_hash FROM users WHERE id = :id"),
            {"id": user_id},
        ).one()
    assert row.name == "Mig User"
    assert row.email == "mig@example.invalid"
    assert row.role == "agent"
    assert row.is_active is True
    assert row.password_hash is None

    down = _alembic(["downgrade", "-1"], cli_env)
    assert down.returncode == 0, down.stderr
    assert _COLUMN not in _user_columns(migration_db)
    with migration_db.connect() as conn:
        survived = conn.execute(
            text("SELECT email FROM users WHERE id = :id"),
            {"id": user_id},
        ).scalar_one()
    assert survived == "mig@example.invalid"

    up_again = _alembic(["upgrade", "head"], cli_env)
    assert up_again.returncode == 0, up_again.stderr
    assert _user_columns(migration_db)[_COLUMN] is True
    with migration_db.connect() as conn:
        hash_again = conn.execute(
            text("SELECT password_hash FROM users WHERE id = :id"),
            {"id": user_id},
        ).scalar_one()
    assert hash_again is None
