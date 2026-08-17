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
from surplus_ai.database.migrations.versions.d4c8a1b9e703_p3b1_auth_sessions import (
    down_revision,
    revision,
)
from surplus_ai.database.migrator import head_revision
from tests.conftest import TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PREV = "fc21ee2dd624"
_TABLE = "auth_sessions"
_UNIQUE = "uq_auth_sessions_token_digest"


def test_p3b1_migration_revises_single_current_head() -> None:
    assert down_revision == _PREV
    assert revision == "d4c8a1b9e703"
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


def _table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _unique_names(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_unique_constraints(table)}


def _fk_ondelete(engine: Engine, table: str, constrained: str) -> str | None:
    for fk in inspect(engine).get_foreign_keys(table):
        if constrained in fk.get("constrained_columns", []):
            return fk.get("options", {}).get("ondelete")
    return None


def test_p3b1_auth_sessions_migration_lifecycle(
    migration_db: Engine, cli_env: dict[str, str]
) -> None:
    up_prev = _alembic(["upgrade", _PREV], cli_env)
    assert up_prev.returncode == 0, up_prev.stderr
    assert _TABLE not in _table_names(migration_db)

    user_id = uuid.uuid4()
    with migration_db.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO users (id, name, email, role, is_active, password_hash)
                VALUES (:id, 'Mig User', 'mig-sess@example.invalid', 'agent', true, NULL)
                """
            ),
            {"id": user_id},
        )

    up_head = _alembic(["upgrade", "head"], cli_env)
    assert up_head.returncode == 0, up_head.stderr
    assert _TABLE in _table_names(migration_db)
    columns = {c["name"]: c for c in inspect(migration_db).get_columns(_TABLE)}
    assert set(columns) == {"id", "user_id", "token_digest", "created_at", "expires_at"}
    assert columns["token_digest"]["nullable"] is False
    assert columns["expires_at"]["nullable"] is False
    assert columns["user_id"]["nullable"] is False
    assert _UNIQUE in _unique_names(migration_db, _TABLE)
    assert _fk_ondelete(migration_db, _TABLE, "user_id") == "CASCADE"

    with migration_db.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO auth_sessions (id, user_id, token_digest, expires_at)
                VALUES (:id, :user_id, :digest, now() + interval '12 hours')
                """
            ),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "digest": "a" * 64,
            },
        )

    with migration_db.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        remaining = conn.execute(text("SELECT COUNT(*) FROM auth_sessions")).scalar_one()
    assert int(remaining) == 0

    with migration_db.connect() as conn:
        hash_col = conn.execute(
            text(
                """
                SELECT is_nullable FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'password_hash'
                """
            )
        ).scalar_one()
    assert hash_col == "YES"

    down = _alembic(["downgrade", "-1"], cli_env)
    assert down.returncode == 0, down.stderr
    assert _TABLE not in _table_names(migration_db)
    assert "password_hash" in {c["name"] for c in inspect(migration_db).get_columns("users")}

    up_again = _alembic(["upgrade", "head"], cli_env)
    assert up_again.returncode == 0, up_again.stderr
    assert _TABLE in _table_names(migration_db)
