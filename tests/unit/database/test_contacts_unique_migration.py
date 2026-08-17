from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

import surplus_ai.database.models  # noqa: F401
from surplus_ai.database.base import Base
from surplus_ai.database.migrations.versions.f3a8d12e90b1_phase6f_a_contacts_unique import (
    down_revision,
    revision,
)
from surplus_ai.database.migrator import head_revision
from tests.conftest import TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONSTRAINT = "uq_contacts_lead_contact_type_value"
_PREV = "e7b2c91f4a60"


def test_phase6f_a_migration_revises_single_current_head() -> None:
    assert down_revision == _PREV
    assert revision == "f3a8d12e90b1"
    # Head advanced by later revisions; 6F-A remains an ancestor, not the current head.
    assert head_revision() == "d4c8a1b9e703"


@pytest.fixture
def cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["SURPLUS_AI_DATABASE_URL"] = TEST_DATABASE_URL
    env["SURPLUS_AI_ENV"] = "dev"
    env["SURPLUS_AI_LOG_LEVEL"] = "WARNING"
    return env


@pytest.fixture
def migration_db(engine: Engine) -> Iterator[Engine]:
    """Isolated public schema for alembic upgrade/downgrade of this revision only."""
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    # Restore ORM schema for other session-scoped unit tests sharing this engine.
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


def _unique_names(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_unique_constraints(table)}


def test_phase6f_a_migration_lifecycle_constraint(
    migration_db: Engine, cli_env: dict[str, str]
) -> None:
    up_prev = _alembic(["upgrade", _PREV], cli_env)
    assert up_prev.returncode == 0, up_prev.stderr
    assert _CONSTRAINT not in _unique_names(migration_db, "contacts")

    up_rev = _alembic(["upgrade", revision], cli_env)
    assert up_rev.returncode == 0, up_rev.stderr
    assert _CONSTRAINT in _unique_names(migration_db, "contacts")

    with migration_db.begin() as conn:
        county_id = conn.execute(
            text(
                """
                INSERT INTO counties (
                    id, slug, name, state, fips_code, source_type,
                    parsing_profile_key, compliance_state_ref,
                    publishing_frequency, is_active
                ) VALUES (
                    :id, 'mig-6fa', 'Mig County', 'MD', '24999',
                    'manual_upload', 'md-mig', 'MD', 'irregular', true
                ) RETURNING id
                """
            ),
            {"id": uuid.uuid4()},
        ).scalar_one()
        case_id = conn.execute(
            text(
                """
                INSERT INTO surplus_cases (
                    id, county_id, parcel_id, property_address_raw,
                    surplus_is_explicit, surplus_source, status, dedupe_hash
                ) VALUES (
                    :id, :county_id, 'P-1', '1 MAIN',
                    true, 'explicit', 'normalized', 'mig-6fa-hash'
                ) RETURNING id
                """
            ),
            {"id": uuid.uuid4(), "county_id": county_id},
        ).scalar_one()
        lead_id = conn.execute(
            text(
                """
                INSERT INTO leads (id, surplus_case_id, status)
                VALUES (:id, :case_id, 'qualified')
                RETURNING id
                """
            ),
            {"id": uuid.uuid4(), "case_id": case_id},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO contacts (
                    id, lead_id, contact_type, value, is_verified
                ) VALUES (
                    :id, :lead_id, 'phone', '+15555550100', false
                )
                """
            ),
            {"id": uuid.uuid4(), "lead_id": lead_id},
        )

    with pytest.raises(IntegrityError):
        with migration_db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO contacts (
                        id, lead_id, contact_type, value, is_verified
                    ) VALUES (
                        :id, :lead_id, 'phone', '+15555550100', false
                    )
                    """
                ),
                {"id": uuid.uuid4(), "lead_id": lead_id},
            )

    down = _alembic(["downgrade", _PREV], cli_env)
    assert down.returncode == 0, down.stderr
    assert _CONSTRAINT not in _unique_names(migration_db, "contacts")

    up_again = _alembic(["upgrade", revision], cli_env)
    assert up_again.returncode == 0, up_again.stderr
    assert _CONSTRAINT in _unique_names(migration_db, "contacts")
