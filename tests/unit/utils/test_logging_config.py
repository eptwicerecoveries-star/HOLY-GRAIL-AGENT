from __future__ import annotations

import json
import logging
from pathlib import Path

import structlog

from surplus_ai.utils.config import Settings
from surplus_ai.utils.logging_config import configure_logging

VALID_URL = "postgresql+psycopg://u:p@localhost:5432/db"


def _settings(tmp_path: Path, level: str = "INFO") -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        env="test",
        database_url=VALID_URL,
        log_level=level,
        log_dir=tmp_path / "logs",
    )


def test_creates_log_directory_and_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    configure_logging(settings)
    structlog.get_logger("t").info("event_one")
    logging.shutdown()

    log_file = settings.log_dir / "surplus_ai.log"
    assert settings.log_dir.is_dir()
    assert log_file.is_file()


def test_emits_valid_json_with_expected_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    configure_logging(settings)

    structlog.get_logger("t").info("ingest_started", county="demo", rows=7)
    logging.shutdown()

    lines = (settings.log_dir / "surplus_ai.log").read_text().strip().splitlines()
    payload = json.loads(lines[-1])

    assert payload["event"] == "ingest_started"
    assert payload["county"] == "demo"
    assert payload["rows"] == 7
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_respects_configured_level(tmp_path: Path) -> None:
    settings = _settings(tmp_path, level="WARNING")
    configure_logging(settings)

    log = structlog.get_logger("t")
    log.info("should_be_filtered")
    log.warning("should_be_recorded")
    logging.shutdown()

    contents = (settings.log_dir / "surplus_ai.log").read_text()
    assert "should_be_filtered" not in contents
    assert "should_be_recorded" in contents


def test_exception_info_is_rendered(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    configure_logging(settings)

    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger("t").exception("operation_failed")
    logging.shutdown()

    payload = json.loads((settings.log_dir / "surplus_ai.log").read_text().strip().splitlines()[-1])
    assert payload["event"] == "operation_failed"
    assert "ValueError: boom" in payload["exception"]


def test_reconfiguration_does_not_duplicate_handlers(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    configure_logging(settings)
    configure_logging(settings)
    structlog.get_logger("t").info("only_once")
    logging.shutdown()

    lines = (settings.log_dir / "surplus_ai.log").read_text().strip().splitlines()
    assert sum("only_once" in line for line in lines) == 1
