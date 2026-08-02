from __future__ import annotations

import logging
import logging.handlers

import structlog

from surplus_ai.utils.config import Settings


def configure_logging(settings: Settings) -> None:
    """Wire structlog (JSON events) through stdlib logging (rotating file + stream)."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = settings.log_dir / "surplus_ai.log"
    level = getattr(logging, settings.log_level)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
    )
    stream_handler = logging.StreamHandler()

    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[file_handler, stream_handler],
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
