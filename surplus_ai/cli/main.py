from __future__ import annotations

import typer

from surplus_ai import __version__
from surplus_ai.cli.commands import classify as classify_commands
from surplus_ai.cli.commands import db as db_commands
from surplus_ai.cli.commands import parser as parser_commands
from surplus_ai.utils.config import ConfigurationError, get_settings
from surplus_ai.utils.logging_config import configure_logging


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


def build_app() -> typer.Typer:
    """Assemble the root Typer application and register every command group."""
    app = typer.Typer(help="SurplusAI — surplus funds recovery operating system.")
    app.add_typer(db_commands.app, name="db")
    app.add_typer(parser_commands.app, name="parser")
    app.add_typer(classify_commands.app, name="classify")

    @app.callback()
    def _root(
        version: bool = typer.Option(
            False,
            "--version",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ) -> None:
        try:
            configure_logging(get_settings())
        except ConfigurationError as exc:
            typer.echo(f"Configuration error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    return app


app = build_app()


if __name__ == "__main__":
    app()
