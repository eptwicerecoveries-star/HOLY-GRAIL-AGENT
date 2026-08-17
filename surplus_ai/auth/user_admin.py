"""Local User create/reset helpers. Callers own the database transaction."""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.auth.exceptions import UserAdminError, UserAlreadyExistsError, UserNotFoundError
from surplus_ai.auth.passwords import hash_password, validate_password, verify_password
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.user import User

logger = structlog.get_logger(__name__)


def create_user(
    session: Session,
    *,
    name: str,
    email: str,
    role: UserRole,
    password: str,
) -> User:
    """Insert an internal User with a password hash. Does not commit."""
    if not isinstance(name, str) or name == "":
        raise UserAdminError("Name is required.")
    if not isinstance(email, str) or email == "":
        raise UserAdminError("Email is required.")
    validate_password(password)
    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise UserAlreadyExistsError("A user with that email already exists.")
    user = User(
        name=name,
        email=email,
        role=role,
        is_active=True,
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    logger.info(
        "user_created",
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
    )
    return user


def reset_user_password(session: Session, *, email: str, password: str) -> User:
    """Replace password_hash for an existing User. Does not create a User. Does not commit."""
    if not isinstance(email, str) or email == "":
        raise UserAdminError("Email is required.")
    validate_password(password)
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        raise UserNotFoundError("No user found with that email.")
    user.password_hash = hash_password(password)
    session.flush()
    logger.info("user_password_reset", user_id=str(user.id), email=user.email)
    return user


def verify_user_credentials(user: User, password: str) -> bool:
    """Fail-closed local credential check. Not HTTP authentication.

    Returns False when the user is inactive or password_hash is NULL.
    """
    if not user.is_active:
        return False
    if user.password_hash is None:
        return False
    return verify_password(password, user.password_hash)
