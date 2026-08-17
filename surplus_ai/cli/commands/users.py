"""Local internal-user administration. No HTTP signup or password reset."""

from __future__ import annotations

import typer

from surplus_ai.auth.exceptions import (
    AuthDependencyError,
    AuthError,
    PasswordPolicyError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from surplus_ai.auth.user_admin import create_user, reset_user_password
from surplus_ai.database.engine import session_scope
from surplus_ai.database.models.enums import UserRole
from surplus_ai.utils.exceptions import AppError

app = typer.Typer(help="Create and reset local internal users (no HTTP authentication).")


def _prompt_new_password() -> str:
    return str(typer.prompt("Password", hide_input=True, confirmation_prompt=True))


@app.command("create")
def create_cmd(
    name: str = typer.Option(..., "--name", help="Display name."),
    email: str = typer.Option(..., "--email", help="Unique User.email (stored as entered)."),
    role: UserRole = typer.Option(
        ...,
        "--role",
        help="Existing UserRole: admin, manager, or agent.",
    ),
) -> None:
    """Create an internal User with a hashed password. Does not overwrite existing emails."""
    password = _prompt_new_password()
    try:
        with session_scope() as session:
            user = create_user(session, name=name, email=email, role=role, password=password)
            user_id = user.id
            user_email = user.email
            user_role = user.role.value
            user_active = user.is_active
    except PasswordPolicyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except UserAlreadyExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except AuthDependencyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (AppError, AuthError) as exc:
        typer.echo(f"Could not create user: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"user_id:   {user_id}")
    typer.echo(f"email:     {user_email}")
    typer.echo(f"role:      {user_role}")
    typer.echo(f"is_active: {user_active}")


@app.command("reset-password")
def reset_password_cmd(
    email: str = typer.Option(..., "--email", help="Canonical User.email to reset."),
) -> None:
    """Replace an existing User's password hash. Does not create a User."""
    password = _prompt_new_password()
    try:
        with session_scope() as session:
            user = reset_user_password(session, email=email, password=password)
            user_id = user.id
            user_email = user.email
            user_role = user.role.value
            user_active = user.is_active
    except PasswordPolicyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except UserNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except AuthDependencyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (AppError, AuthError) as exc:
        typer.echo(f"Could not reset password: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"user_id:   {user_id}")
    typer.echo(f"email:     {user_email}")
    typer.echo(f"role:      {user_role}")
    typer.echo(f"is_active: {user_active}")
