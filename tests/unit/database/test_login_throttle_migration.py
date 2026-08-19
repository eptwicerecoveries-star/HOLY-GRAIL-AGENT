from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text

from surplus_ai.database.base import Base
from surplus_ai.database.migrations.versions.d4c8a1b9e703_p3b1_auth_sessions import (
    revision as p3b1_revision,
)
from surplus_ai.database.migrations.versions.e1f4a8c92b03_p4b2a_login_throttle_buckets import (
    down_revision,
    revision,
)
from surplus_ai.database.migrator import head_revision
from tests.conftest import TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TABLE = "login_throttle_buckets"
_PREV = p3b1_revision


def test_p4b2a_migration_revises_p3b1_head() -> None:
    assert down_revision == _PREV
    assert revision == "e1f4a8c92b03"
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


def _check_names(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_check_constraints(table)}


def test_p4b2a_login_throttle_migration_lifecycle(
    migration_db: Engine, cli_env: dict[str, str]
) -> None:
    up_prev = _alembic(["upgrade", _PREV], cli_env)
    assert up_prev.returncode == 0, up_prev.stderr
    assert _TABLE not in _table_names(migration_db)
    assert "auth_sessions" in _table_names(migration_db)
    assert "users" in _table_names(migration_db)

    up_head = _alembic(["upgrade", "head"], cli_env)
    assert up_head.returncode == 0, up_head.stderr
    assert _TABLE in _table_names(migration_db)
    columns = {c["name"]: c for c in inspect(migration_db).get_columns(_TABLE)}
    assert set(columns) == {
        "scope",
        "key_digest",
        "window_started_at",
        "event_count",
        "blocked_until",
    }
    assert columns["scope"]["nullable"] is False
    assert columns["key_digest"]["nullable"] is False
    assert columns["window_started_at"]["nullable"] is False
    assert columns["event_count"]["nullable"] is False
    assert columns["blocked_until"]["nullable"] is True
    pk = inspect(migration_db).get_pk_constraint(_TABLE)
    assert pk["constrained_columns"] == ["scope", "key_digest"]
    checks = _check_names(migration_db, _TABLE)
    assert "ck_login_throttle_buckets_event_count" in checks
    assert "ck_login_throttle_buckets_scope" in checks
    assert inspect(migration_db).get_indexes(_TABLE) == []
    assert columns["window_started_at"]["type"].timezone is True
    assert columns["blocked_until"]["type"].timezone is True
    assert getattr(columns["scope"]["type"], "length", None) == 16
    assert getattr(columns["key_digest"]["type"], "length", None) == 64

    with migration_db.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO login_throttle_buckets
                    (scope, key_digest, window_started_at, event_count, blocked_until)
                VALUES
                    ('ip', :digest, now(), 1, NULL)
                """
            ),
            {"digest": "a" * 64},
        )

    down = _alembic(["downgrade", "-1"], cli_env)
    assert down.returncode == 0, down.stderr
    assert _TABLE not in _table_names(migration_db)
    assert "auth_sessions" in _table_names(migration_db)
    assert "users" in _table_names(migration_db)

    up_again = _alembic(["upgrade", "head"], cli_env)
    assert up_again.returncode == 0, up_again.stderr
    assert _TABLE in _table_names(migration_db)
