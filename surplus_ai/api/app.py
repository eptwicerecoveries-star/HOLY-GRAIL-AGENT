"""FastAPI application factory for the local read-only Holy Grail API and dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from surplus_ai.api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from surplus_ai.api.routes import cases, contacts, dashboard, health, leads, reviews, status
from surplus_ai.api.static import DASHBOARD_SECURITY_HEADERS, STATIC_DIR
from surplus_ai.utils.config import get_settings
from surplus_ai.utils.logging_config import configure_logging


class DashboardSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach conservative headers to dashboard HTML and static assets only."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            for key, value in DASHBOARD_SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response


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
            "Local-development read-only API and operator dashboard for the Holy Grail "
            "surplus engine. UNAUTHENTICATED — must not be internet-facing. "
            "Default bind: 127.0.0.1."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
    application.add_middleware(DashboardSecurityHeadersMiddleware)

    application.include_router(dashboard.router)
    application.include_router(health.router)
    application.include_router(status.router, prefix="/api/v1")
    application.include_router(cases.router, prefix="/api/v1")
    application.include_router(leads.router, prefix="/api/v1")
    application.include_router(reviews.router, prefix="/api/v1")
    application.include_router(contacts.router, prefix="/api/v1")
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
