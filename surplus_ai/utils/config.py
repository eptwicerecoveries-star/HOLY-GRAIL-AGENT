from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from surplus_ai.utils.exceptions import AppError


class ConfigurationError(AppError):
    """Raised when application settings fail to load or fail validation."""


class Settings(BaseSettings):
    """Layered application configuration: env vars (SURPLUS_AI_*) override .env."""

    model_config = SettingsConfigDict(
        env_prefix="SURPLUS_AI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path = Path("logs")

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use the 'postgresql+psycopg://' driver scheme")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton, loading it on first use."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(f"Failed to load application settings: {exc}") from exc
