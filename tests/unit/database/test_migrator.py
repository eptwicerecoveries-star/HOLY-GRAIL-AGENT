from __future__ import annotations

import pytest
from alembic.config import Config

from surplus_ai.database.migrator import (
    ALEMBIC_INI,
    MigrationError,
    build_alembic_config,
    head_revision,
)
from surplus_ai.utils.exceptions import AppError


def test_migration_error_is_an_app_error() -> None:
    assert issubclass(MigrationError, AppError)


def test_alembic_ini_exists_at_project_root() -> None:
    assert ALEMBIC_INI.is_file()


def test_build_alembic_config_returns_usable_config() -> None:
    config = build_alembic_config()

    assert isinstance(config, Config)
    assert config.get_main_option("script_location", "").endswith("database/migrations")


def test_head_revision_is_resolvable() -> None:
    assert head_revision() is not None


def test_build_alembic_config_raises_when_ini_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import surplus_ai.database.migrator as migrator

    monkeypatch.setattr(migrator, "ALEMBIC_INI", migrator.PROJECT_ROOT / "no_such.ini")

    with pytest.raises(MigrationError, match="alembic.ini not found"):
        migrator.build_alembic_config()
