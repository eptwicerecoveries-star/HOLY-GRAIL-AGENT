"""P3-A Typer user bootstrap/reset. Fake secrets only."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from click.testing import Result
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from surplus_ai.auth.passwords import verify_password
from surplus_ai.auth.user_admin import create_user
from surplus_ai.cli.main import build_app
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.user import User

_FAKE_OK = "test-passphrase-ok"
_FAKE_NEXT = "next-passphrase-ok"
_PROMPT = f"{_FAKE_OK}\n{_FAKE_OK}\n"
_PROMPT_NEXT = f"{_FAKE_NEXT}\n{_FAKE_NEXT}\n"


@pytest.fixture
def invoke_users(session: Session, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Result]:
    @contextmanager
    def _scope() -> Iterator[Session]:
        yield session
        session.flush()

    monkeypatch.setattr("surplus_ai.cli.commands.users.session_scope", _scope)

    def _invoke(args: list[str], input_text: str | None = None) -> Result:
        return CliRunner().invoke(build_app(), args, input=input_text)

    return _invoke


def _text(result: Result) -> str:
    return result.stdout + result.stderr


def test_cli_help_does_not_require_password_prompt(invoke_users: Callable[..., Result]) -> None:
    root = invoke_users(["--help"])
    users = invoke_users(["users", "--help"])
    assert root.exit_code == 0
    assert users.exit_code == 0
    assert "create" in _text(users)
    assert "reset-password" in _text(users)


def test_cli_create_user_hidden_prompt(
    invoke_users: Callable[..., Result], session: Session
) -> None:
    result = invoke_users(
        [
            "users",
            "create",
            "--name",
            "Ada",
            "--email",
            "cli@example.invalid",
            "--role",
            "agent",
        ],
        input_text=_PROMPT,
    )
    output = _text(result)
    assert result.exit_code == 0, output
    assert _FAKE_OK not in output
    assert "password_hash" not in output
    assert "$argon2" not in output
    user = session.scalar(select(User).where(User.email == "cli@example.invalid"))
    assert user is not None
    assert user.role is UserRole.AGENT
    assert user.is_active is True
    assert user.password_hash is not None
    assert verify_password(_FAKE_OK, user.password_hash)
    assert str(user.id) in output
    assert "cli@example.invalid" in output


def test_cli_create_rejects_password_argument(invoke_users: Callable[..., Result]) -> None:
    result = invoke_users(
        [
            "users",
            "create",
            "--name",
            "Ada",
            "--email",
            "arg@example.invalid",
            "--role",
            "agent",
            "--password",
            _FAKE_OK,
        ]
    )
    assert result.exit_code != 0
    assert _FAKE_OK not in _text(result)


def test_cli_create_duplicate_email_fails(
    invoke_users: Callable[..., Result], session: Session
) -> None:
    first = invoke_users(
        ["users", "create", "--name", "Ada", "--email", "dup@example.invalid", "--role", "admin"],
        input_text=_PROMPT,
    )
    assert first.exit_code == 0, _text(first)
    second = invoke_users(
        ["users", "create", "--name", "Bob", "--email", "dup@example.invalid", "--role", "agent"],
        input_text=_PROMPT_NEXT,
    )
    assert second.exit_code == 1
    assert "already exists" in _text(second)
    assert _FAKE_NEXT not in _text(second)
    user = session.scalar(select(User).where(User.email == "dup@example.invalid"))
    assert user is not None
    assert user.name == "Ada"
    assert user.role is UserRole.ADMIN
    assert verify_password(_FAKE_OK, user.password_hash or "")
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_cli_reset_password(
    invoke_users: Callable[..., Result], session: Session
) -> None:
    user = create_user(
        session,
        name="Ada",
        email="reset-cli@example.invalid",
        role=UserRole.MANAGER,
        password=_FAKE_OK,
    )
    user_id = user.id
    result = invoke_users(
        ["users", "reset-password", "--email", "reset-cli@example.invalid"],
        input_text=_PROMPT_NEXT,
    )
    output = _text(result)
    assert result.exit_code == 0, output
    assert _FAKE_OK not in output
    assert _FAKE_NEXT not in output
    assert "password_hash" not in output
    assert "$argon2" not in output
    session.refresh(user)
    assert user.id == user_id
    assert user.role is UserRole.MANAGER
    assert user.name == "Ada"
    assert not verify_password(_FAKE_OK, user.password_hash or "")
    assert verify_password(_FAKE_NEXT, user.password_hash or "")


def test_cli_reset_missing_user(invoke_users: Callable[..., Result], session: Session) -> None:
    result = invoke_users(
        ["users", "reset-password", "--email", "missing@example.invalid"],
        input_text=_PROMPT,
    )
    assert result.exit_code == 1
    assert "No user found" in _text(result)
    assert session.scalar(select(func.count()).select_from(User)) == 0
    assert _FAKE_OK not in _text(result)


def test_cli_create_rejects_unknown_role(invoke_users: Callable[..., Result]) -> None:
    result = invoke_users(
        [
            "users",
            "create",
            "--name",
            "Ada",
            "--email",
            "role@example.invalid",
            "--role",
            "reviewer",
        ],
        input_text=_PROMPT,
    )
    assert result.exit_code != 0
