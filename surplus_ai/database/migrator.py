from __future__ import annotations

from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from surplus_ai.database.engine import get_engine
from surplus_ai.utils.exceptions import AppError

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


class MigrationError(AppError):
    """Raised when an Alembic migration operation fails."""


def build_alembic_config() -> Config:
    """Load alembic.ini from the project root."""
    if not ALEMBIC_INI.is_file():
        raise MigrationError(f"alembic.ini not found at {ALEMBIC_INI}")
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "surplus_ai/database/migrations"))
    return config


def upgrade_to_head() -> None:
    """Apply every pending migration."""
    logger.info("migration_upgrade_start", target="head")
    try:
        command.upgrade(build_alembic_config(), "head")
    except Exception as exc:
        logger.error("migration_upgrade_failed", error=str(exc))
        raise MigrationError(f"Migration upgrade failed: {exc}") from exc
    logger.info("migration_upgrade_complete", revision=current_revision())


def downgrade_to(revision: str) -> None:
    """Roll back to the given revision."""
    logger.info("migration_downgrade_start", target=revision)
    try:
        command.downgrade(build_alembic_config(), revision)
    except Exception as exc:
        logger.error("migration_downgrade_failed", error=str(exc))
        raise MigrationError(f"Migration downgrade failed: {exc}") from exc
    logger.info("migration_downgrade_complete", revision=current_revision())


def current_revision() -> str | None:
    """Return the revision currently applied to the database, or None if unmigrated."""
    with get_engine().connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str | None:
    """Return the latest revision available in the migrations directory."""
    return ScriptDirectory.from_config(build_alembic_config()).get_current_head()


def is_up_to_date() -> bool:
    """True when the database is at the newest available revision."""
    return current_revision() == head_revision()
