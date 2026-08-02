from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text

from tests.conftest import TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLE_COUNT = 20
ENUM_TYPE_COUNT = 15


@pytest.fixture
def cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["SURPLUS_AI_DATABASE_URL"] = TEST_DATABASE_URL
    env["SURPLUS_AI_ENV"] = "dev"
    env["SURPLUS_AI_LOG_LEVEL"] = "WARNING"
    return env


@pytest.fixture
def clean_db(engine: Engine) -> Iterator[Engine]:
    """Drop and recreate the public schema so migrations run from nothing."""
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "surplus_ai.cli.main", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def enum_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM pg_type WHERE typtype = 'e'")
        ).scalar_one()


def test_db_init_creates_full_schema(clean_db: Engine, cli_env: dict[str, str]) -> None:
    result = run_cli(["db", "init"], cli_env)

    assert result.returncode == 0, result.stderr
    names = table_names(clean_db)
    assert "alembic_version" in names
    assert len(names - {"alembic_version"}) == EXPECTED_TABLE_COUNT


def test_db_init_is_idempotent(clean_db: Engine, cli_env: dict[str, str]) -> None:
    assert run_cli(["db", "init"], cli_env).returncode == 0
    second = run_cli(["db", "init"], cli_env)

    assert second.returncode == 0, second.stderr
    assert len(table_names(clean_db) - {"alembic_version"}) == EXPECTED_TABLE_COUNT


def test_db_status_reports_pending_then_current(clean_db: Engine, cli_env: dict[str, str]) -> None:
    before = run_cli(["db", "status"], cli_env)
    assert "PENDING MIGRATIONS" in before.stdout

    run_cli(["db", "init"], cli_env)
    after = run_cli(["db", "status"], cli_env)

    assert "up to date" in after.stdout
    assert after.returncode == 0


def test_migration_downgrade_removes_tables_and_enum_types(
    clean_db: Engine, cli_env: dict[str, str]
) -> None:
    run_cli(["db", "init"], cli_env)
    assert enum_count(clean_db) == ENUM_TYPE_COUNT

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=PROJECT_ROOT,
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert downgrade.returncode == 0, downgrade.stderr
    assert table_names(clean_db) == {"alembic_version"}
    assert enum_count(clean_db) == 0


def test_upgrade_after_downgrade_succeeds(clean_db: Engine, cli_env: dict[str, str]) -> None:
    """Regression guard: leftover enum types used to break the second upgrade."""
    run_cli(["db", "init"], cli_env)
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=PROJECT_ROOT,
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    second = run_cli(["db", "init"], cli_env)

    assert second.returncode == 0, second.stderr
    assert len(table_names(clean_db) - {"alembic_version"}) == EXPECTED_TABLE_COUNT


def test_migration_matches_orm_models(clean_db: Engine, cli_env: dict[str, str]) -> None:
    """`alembic check` proves the migration and the ORM metadata have not drifted apart."""
    run_cli(["db", "init"], cli_env)

    check = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=PROJECT_ROOT,
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert check.returncode == 0, f"{check.stdout}\n{check.stderr}"
    assert "No new upgrade operations detected" in check.stdout + check.stderr


def test_seed_command_populates_and_is_idempotent(
    clean_db: Engine, cli_env: dict[str, str]
) -> None:
    run_cli(["db", "init"], cli_env)

    first = run_cli(["db", "seed"], cli_env)
    second = run_cli(["db", "seed"], cli_env)

    assert "2 row(s) created" in first.stdout
    assert "0 row(s) created" in second.stdout
    with clean_db.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 2


def test_seed_refused_outside_dev(clean_db: Engine, cli_env: dict[str, str]) -> None:
    run_cli(["db", "init"], cli_env)
    prod_env = dict(cli_env)
    prod_env["SURPLUS_AI_ENV"] = "prod"

    result = run_cli(["db", "seed"], prod_env)

    assert result.returncode == 1
    assert "Refusing to seed" in result.stderr
    with clean_db.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 0


def test_backup_writes_restorable_archive(
    clean_db: Engine, cli_env: dict[str, str], tmp_path: Path
) -> None:
    run_cli(["db", "init"], cli_env)
    run_cli(["db", "seed"], cli_env)
    target = tmp_path / "backup.dump"

    result = run_cli(["db", "backup", str(target)], cli_env)

    assert result.returncode == 0, result.stderr
    assert target.is_file() and target.stat().st_size > 0

    listing = subprocess.run(
        ["pg_restore", "--list", str(target)], capture_output=True, text=True, timeout=120
    )
    assert listing.returncode == 0
    assert "users" in listing.stdout


def test_cli_fails_clearly_on_unreachable_database(cli_env: dict[str, str]) -> None:
    broken = dict(cli_env)
    broken["SURPLUS_AI_DATABASE_URL"] = (
        "postgresql+psycopg://surplus_ai:surplus_ai_dev@localhost:65433/nope"
    )

    result = run_cli(["db", "init"], broken)

    assert result.returncode == 1


def test_cli_fails_clearly_on_invalid_configuration(cli_env: dict[str, str]) -> None:
    broken = dict(cli_env)
    broken["SURPLUS_AI_DATABASE_URL"] = "mysql://user:pass@localhost/db"

    result = run_cli(["db", "status"], broken)

    assert result.returncode == 1
    assert "Configuration error" in result.stderr


def test_version_flag_works_without_a_subcommand(cli_env: dict[str, str]) -> None:
    result = run_cli(["--version"], cli_env)

    assert result.returncode == 0
    assert result.stdout.strip() == "0.1.0"
