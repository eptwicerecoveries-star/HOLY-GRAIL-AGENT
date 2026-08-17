"""Same-origin checks for local auth POSTs. Not CORS."""

from __future__ import annotations

from typing import Any, cast

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from surplus_ai.api.errors import error_body

# Canonical local UI origin. Do not accept localhost or other ports.
ALLOWED_AUTH_ORIGIN = "http://127.0.0.1:8000"


def require_allowed_auth_origin(request: Request) -> None:
    """Reject auth POSTs without an exact Origin match."""
    origin = request.headers.get("origin")
    if origin != ALLOWED_AUTH_ORIGIN:
        raise StarletteHTTPException(
            status_code=403,
            detail=cast(
                Any,
                error_body(
                    code="origin_not_allowed",
                    message="Request origin is not allowed.",
                ),
            ),
        )
