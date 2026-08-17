"""FastAPI application factory for Productization P1 (local read-only API)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from surplus_ai.api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from surplus_ai.api.routes import cases, contacts, health, leads, reviews, status
from surplus_ai.utils.config import get_settings
from surplus_ai.utils.logging_config import configure_logging


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings())
    yield


def create_app() -> FastAPI:
    """Build the local-development ASGI application.

    CORS is intentionally disabled. No authentication is configured.
    Do not bind this process to a public interface.
    """
    application = FastAPI(
        title="Holy Grail API",
        description=(
            "Local-development read-only API for the Holy Grail surplus engine. "
            "UNAUTHENTICATED — must not be internet-facing. Default bind: 127.0.0.1."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)

    application.include_router(health.router)
    application.include_router(status.router, prefix="/api/v1")
    application.include_router(cases.router, prefix="/api/v1")
    application.include_router(leads.router, prefix="/api/v1")
    application.include_router(reviews.router, prefix="/api/v1")
    application.include_router(contacts.router, prefix="/api/v1")
    return application


app = create_app()
