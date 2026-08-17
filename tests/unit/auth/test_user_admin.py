"""P3-A local user-admin helpers. Fake secrets only. No HTTP authentication."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.auth.exceptions import UserAlreadyExistsError, UserNotFoundError
from surplus_ai.auth.passwords import verify_password
from surplus_ai.auth.user_admin import (
    create_user,
    reset_user_password,
    verify_user_credentials,
)
from surplus_ai.database.models.audit_log import AuditLog
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.user import User

_FAKE_OK = "test-passphrase-ok"
_FAKE_NEXT = "next-passphrase-ok"


def test_create_user_stores_hash_only(session: Session) -> None:
    user = create_user(
        session,
        name="Ada",
        email="ada@example.invalid",
        role=UserRole.ADMIN,
        password=_FAKE_OK,
    )
    assert user.password_hash is not None
    assert user.password_hash != _FAKE_OK
    assert _FAKE_OK not in user.password_hash
    assert user.email == "ada@example.invalid"
    assert user.role is UserRole.ADMIN
    assert user.is_active is True
    assert verify_password(_FAKE_OK, user.password_hash)
    assert "password" not in User.__table__.c
    assert "password_salt" not in User.__table__.c


def test_create_user_duplicate_email_does_not_overwrite(session: Session) -> None:
    first = create_user(
        session,
        name="Ada",
        email="dup@example.invalid",
        role=UserRole.AGENT,
        password=_FAKE_OK,
    )
    original_id = first.id
    original_hash = first.password_hash
    try:
        create_user(
            session,
            name="Other",
            email="dup@example.invalid",
            role=UserRole.ADMIN,
            password=_FAKE_NEXT,
        )
        raise AssertionError("duplicate email must fail")
    except UserAlreadyExistsError:
        pass
    session.refresh(first)
    assert first.id == original_id
    assert first.password_hash == original_hash
    assert first.name == "Ada"
    assert first.role is UserRole.AGENT
    assert verify_password(_FAKE_OK, first.password_hash or "")
    assert not verify_password(_FAKE_NEXT, first.password_hash or "")


def test_reset_password_preserves_identity(session: Session) -> None:
    user = create_user(
        session,
        name="Ada",
        email="reset@example.invalid",
        role=UserRole.MANAGER,
        password=_FAKE_OK,
    )
    user_id = user.id
    reset = reset_user_password(session, email="reset@example.invalid", password=_FAKE_NEXT)
    assert reset.id == user_id
    assert reset.email == "reset@example.invalid"
    assert reset.role is UserRole.MANAGER
    assert reset.name == "Ada"
    assert not verify_password(_FAKE_OK, reset.password_hash or "")
    assert verify_password(_FAKE_NEXT, reset.password_hash or "")


def test_reset_missing_user_does_not_create(session: Session) -> None:
    try:
        reset_user_password(session, email="missing@example.invalid", password=_FAKE_OK)
        raise AssertionError("missing user must fail")
    except UserNotFoundError:
        pass
    assert session.scalar(select(func.count()).select_from(User)) == 0


def test_email_lookup_is_exact_match(session: Session) -> None:
    create_user(
        session,
        name="Ada",
        email="Case@example.invalid",
        role=UserRole.AGENT,
        password=_FAKE_OK,
    )
    try:
        reset_user_password(session, email="case@example.invalid", password=_FAKE_NEXT)
        raise AssertionError("case-insensitive email lookup must not be introduced")
    except UserNotFoundError:
        pass


def test_null_hash_and_inactive_fail_closed(session: Session) -> None:
    bare = User(name="Bare", email="bare@example.invalid", role=UserRole.AGENT)
    session.add(bare)
    session.flush()
    assert bare.password_hash is None
    assert verify_user_credentials(bare, _FAKE_OK) is False

    active = create_user(
        session,
        name="Active",
        email="active@example.invalid",
        role=UserRole.AGENT,
        password=_FAKE_OK,
    )
    assert verify_user_credentials(active, _FAKE_OK) is True
    active.is_active = False
    session.flush()
    assert verify_user_credentials(active, _FAKE_OK) is False


def test_create_user_does_not_write_audit_log_or_log_secrets(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple[str, dict[str, object]]] = []

    def _info(event: str, **kwargs: object) -> None:
        recorded.append((event, kwargs))

    monkeypatch.setattr("surplus_ai.auth.user_admin.logger.info", _info)
    create_user(
        session,
        name="Ada",
        email="log@example.invalid",
        role=UserRole.AGENT,
        password=_FAKE_OK,
    )
    assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
    blob = str(recorded)
    assert _FAKE_OK not in blob
    assert "password_hash" not in blob
    assert "$argon2" not in blob


def test_password_hash_column_nullable() -> None:
    column = User.__table__.c.password_hash
    assert column.nullable is True
    assert getattr(column.type, "length", None) == 255
