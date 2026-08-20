"""FastAPI application factory for the local authenticated Holy Grail API and dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import Response

from surplus_ai.api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from surplus_ai.api.routes import (
    auth,
    cases,
    contacts,
    dashboard,
    health,
    leads,
    reviews,
    status,
)
from surplus_ai.api.static import (
    DASHBOARD_SECURITY_HEADERS,
    HSTS_HEADER_VALUE,
    STATIC_DIR,
)
from surplus_ai.utils.config import get_settings
from surplus_ai.utils.logging_config import configure_logging


class DashboardSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Browser CSP/frame headers on UI paths; production HSTS on app responses.

    CSP / X-Frame-Options / related headers apply only to ``/``, ``/login``, and
    ``/static/*``. Production HSTS uses ``Settings.env == "prod"`` only — never
    request scheme or forwarded proto. Invalid Host is rejected by
    TrustedHostMiddleware before this middleware runs.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path in {"/", "/login"} or path.startswith("/static/"):
            for key, value in DASHBOARD_SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        if get_settings().env == "prod":
            response.headers.setdefault(
                "Strict-Transport-Security", HSTS_HEADER_VALUE
            )
        return response


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings())
    yield


def create_app() -> FastAPI:
    """Build the local-development ASGI application.

    CORS is intentionally disabled. Session-cookie authentication is localhost-only.
    Do not bind this process to a public interface. Production Origin/Host must be
    explicitly configured; misconfiguration fails during construction.
    """
    settings = get_settings()
    application = FastAPI(
        title="Holy Grail API",
        description=(
            "Local-development read-only API and operator dashboard for the Holy Grail "
            "surplus engine. Session-cookie authenticated — must not be internet-facing. "
            "Default bind: 127.0.0.1. /docs is a local-dev utility only."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
    application.add_middleware(DashboardSecurityHeadersMiddleware)
    # Last added runs first: TrustedHost → dashboard headers → routes.
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(settings.allowed_host_allowlist),
        www_redirect=False,
    )

    application.include_router(dashboard.router)
    application.include_router(health.router)
    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(status.router, prefix="/api/v1")
    application.include_router(cases.router, prefix="/api/v1")
    application.include_router(leads.router, prefix="/api/v1")
    application.include_router(reviews.router, prefix="/api/v1")
    application.include_router(contacts.router, prefix="/api/v1")
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
