from __future__ import annotations

from pathlib import Path

import pytest

from surplus_ai.utils.config import ConfigurationError, Settings, get_settings
from surplus_ai.utils.exceptions import AppError

VALID_URL = "postgresql+psycopg://u:p@localhost:5432/db"


def test_settings_reads_env_prefixed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_ENV", "test")
    monkeypatch.setenv("SURPLUS_AI_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url.get_secret_value() == VALID_URL
    assert settings.auth_origin_allowlist == ("http://127.0.0.1:8000",)
    assert settings.allowed_host_allowlist == ("127.0.0.1", "testserver")


def test_defaults_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.delenv("SURPLUS_AI_ENV", raising=False)
    monkeypatch.delenv("SURPLUS_AI_LOG_LEVEL", raising=False)
    monkeypatch.delenv("SURPLUS_AI_LOG_DIR", raising=False)
    monkeypatch.delenv("SURPLUS_AI_AUTH_ORIGINS", raising=False)
    monkeypatch.delenv("SURPLUS_AI_ALLOWED_HOSTS", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.env == "dev"
    assert settings.log_level == "INFO"
    assert settings.log_dir == Path("logs")
    assert settings.auth_origin_allowlist == ("http://127.0.0.1:8000",)
    assert settings.allowed_host_allowlist == ("127.0.0.1",)


def test_database_url_is_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert "p@localhost" not in repr(settings)
    assert "**" in repr(settings.database_url)


def test_rejects_non_psycopg_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_LOG_LEVEL", "CHATTY")

    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_raises_configuration_error_when_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SURPLUS_AI_DATABASE_URL", raising=False)
    monkeypatch.chdir(Path(__file__).parent)  # away from the project's .env
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError):
        get_settings()

    get_settings.cache_clear()


def test_configuration_error_is_an_app_error() -> None:
    assert issubclass(ConfigurationError, AppError)


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
    get_settings.cache_clear()
