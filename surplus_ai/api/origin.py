"""Same-origin checks for auth POSTs. Not CORS."""

from __future__ import annotations

from typing import Any, cast

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from surplus_ai.api.errors import error_body
from surplus_ai.utils.config import DEFAULT_DEV_AUTH_ORIGIN, get_settings

# Local default Origin string. Runtime allowlist comes from Settings.
ALLOWED_AUTH_ORIGIN = DEFAULT_DEV_AUTH_ORIGIN


def require_allowed_auth_origin(request: Request) -> None:
    """Reject auth POSTs without an exact Origin match against Settings."""
    origin = request.headers.get("origin")
    if origin not in get_settings().auth_origin_allowlist:
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
