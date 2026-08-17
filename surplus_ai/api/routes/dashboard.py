"""Serve the local operator dashboard and login page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from surplus_ai.api.dependencies import get_db_session
from surplus_ai.api.session_cookie import SESSION_COOKIE_NAME
from surplus_ai.api.static import DASHBOARD_HTML, DASHBOARD_SECURITY_HEADERS, LOGIN_HTML
from surplus_ai.auth.sessions import resolve_auth_session

router = APIRouter(tags=["dashboard"])


def _active_session_user(request: Request, session: Session) -> bool:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    return resolve_auth_session(session, raw_token=raw_token) is not None


@router.get("/", include_in_schema=False, response_model=None)
def dashboard_home(
    request: Request,
    session: Session = Depends(get_db_session),
) -> FileResponse | RedirectResponse:
    """Authenticated users get the dashboard; others redirect to /login."""
    if not _active_session_user(request, session):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(
        DASHBOARD_HTML,
        media_type="text/html; charset=utf-8",
        headers=DASHBOARD_SECURITY_HEADERS,
    )


@router.get("/login", include_in_schema=False, response_model=None)
def login_page(
    request: Request,
    session: Session = Depends(get_db_session),
) -> FileResponse | RedirectResponse:
    """Serve the login page; already-authenticated users go to /."""
    if _active_session_user(request, session):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(
        LOGIN_HTML,
        media_type="text/html; charset=utf-8",
        headers=DASHBOARD_SECURITY_HEADERS,
    )
