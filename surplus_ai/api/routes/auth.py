"""Local HTTP login / logout / me (P3-B2). Uses P3-A credentials + P3-B1 sessions."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.api.auth_timing import run_unknown_user_password_check
from surplus_ai.api.dependencies import get_writable_db_session, require_active_user
from surplus_ai.api.errors import authentication_failed
from surplus_ai.api.origin import require_allowed_auth_origin
from surplus_ai.api.schemas import AuthUserResponse, LoginRequest
from surplus_ai.api.session_cookie import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    session_cookie_secure,
    set_session_cookie,
)
from surplus_ai.auth.sessions import create_auth_session, resolve_auth_session, revoke_auth_session
from surplus_ai.auth.user_admin import verify_user_credentials
from surplus_ai.database.models.user import User
from surplus_ai.utils.config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])


def _user_response(user: User) -> AuthUserResponse:
    return AuthUserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
    )


@router.post("/auth/login", response_model=AuthUserResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_writable_db_session),
) -> AuthUserResponse:
    require_allowed_auth_origin(request)
    # Exact email match — no case-fold / normalize (P3-A semantics).
    user = session.scalar(select(User).where(User.email == body.email))
    if user is None:
        run_unknown_user_password_check(body.password)
        logger.info("login_failure")
        raise authentication_failed()
    if not verify_user_credentials(user, body.password):
        logger.info("login_failure")
        raise authentication_failed()

    created = create_auth_session(session, user_id=user.id)
    session.commit()
    secure = session_cookie_secure(env=get_settings().env)
    set_session_cookie(response, raw_token=created.raw_token, secure=secure)
    logger.info("login_success", user_id=str(user.id))
    return _user_response(user)


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_writable_db_session),
) -> dict[str, str]:
    require_allowed_auth_origin(request)
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id: str | None = None
    if raw_token:
        resolved = resolve_auth_session(session, raw_token=raw_token)
        if resolved is not None:
            user_id = str(resolved.user_id)
        revoked = revoke_auth_session(session, raw_token=raw_token)
        if revoked:
            session.commit()
    secure = session_cookie_secure(env=get_settings().env)
    clear_session_cookie(response, secure=secure)
    if user_id is not None:
        logger.info("logout", user_id=user_id)
    else:
        logger.info("logout")
    return {"status": "ok"}


@router.get("/auth/me", response_model=AuthUserResponse)
def me(user: User = Depends(require_active_user)) -> AuthUserResponse:
    return _user_response(user)
