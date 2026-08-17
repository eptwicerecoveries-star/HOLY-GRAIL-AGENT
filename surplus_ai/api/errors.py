"""Safe API error envelope — never leak SQL, paths, secrets, or tracebacks."""

from __future__ import annotations

from typing import Any, cast

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)


def error_body(*, code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


async def http_exception_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    http_exc = cast(StarletteHTTPException, exc)
    detail = http_exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        body = {"code": str(detail["code"]), "message": str(detail["message"])}
    elif isinstance(detail, str):
        code = "not_found" if http_exc.status_code == 404 else "http_error"
        body = error_body(code=code, message=detail)
    else:
        body = error_body(code="http_error", message="Request failed")
    return JSONResponse(status_code=http_exc.status_code, content=body)


async def validation_exception_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    # Do not echo raw body fragments that might contain secrets.
    _ = cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=422,
        content=error_body(code="validation_error", message="Invalid request parameters"),
    )


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("api_unhandled_error", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content=error_body(code="internal_error", message="Internal server error"),
    )


def not_found(resource: str) -> StarletteHTTPException:
    # Starlette typing allows str; we pass a JSON-serializable dict for our envelope.
    return StarletteHTTPException(
        status_code=404,
        detail=cast(Any, error_body(code="not_found", message=f"{resource} not found")),
    )


def authentication_required() -> StarletteHTTPException:
    return StarletteHTTPException(
        status_code=401,
        detail=cast(
            Any,
            error_body(
                code="authentication_required",
                message="Authentication is required.",
            ),
        ),
    )


def authentication_failed() -> StarletteHTTPException:
    return StarletteHTTPException(
        status_code=401,
        detail=cast(
            Any,
            error_body(
                code="authentication_failed",
                message="Invalid email or password.",
            ),
        ),
    )
