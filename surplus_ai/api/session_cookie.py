"""HttpOnly opaque session cookie helpers (P3-B2). No signing secret."""

from __future__ import annotations

from typing import Literal

from starlette.responses import Response

from surplus_ai.auth.sessions import SESSION_LIFETIME

SESSION_COOKIE_NAME = "surplus_ai_session"
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_MAX_AGE = int(SESSION_LIFETIME.total_seconds())  # 43200
SESSION_COOKIE_SAMESITE: Literal["lax"] = "lax"


def session_cookie_secure(*, env: Literal["dev", "test", "prod"]) -> bool:
    """Secure=False only for local HTTP (dev/test). prod always Secure=True."""
    return env == "prod"


def set_session_cookie(
    response: Response,
    *,
    raw_token: str,
    secure: bool,
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_COOKIE_MAX_AGE,
        path=SESSION_COOKIE_PATH,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=secure,
    )


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path=SESSION_COOKIE_PATH,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=secure,
    )
