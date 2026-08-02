from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import structlog
import typer
from sqlalchemy import text

from surplus_ai.database.engine import DatabaseError, get_engine, session_scope
from surplus_ai.database.migrator import (
    MigrationError,
    current_revision,
    head_revision,
    upgrade_to_head,
)
from surplus_ai.database.seed import seed_dev_data
from surplus_ai.utils.config import get_settings
from surplus_ai.utils.exceptions import AppError

logger = structlog.get_logger(__name__)

app = typer.Typer(help="Database lifecycle commands.")


@app.command("init")
def init() -> None:
    """Verify connectivity, then bring the database up to the newest schema revision."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("db_init_connect_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    logger.info("db_init_connected")
    try:
        upgrade_to_head()
    except MigrationError as exc:
        typer.echo(f"Migration failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Database initialized at revision {current_revision()}")


@app.command("migrate")
def migrate() -> None:
    """Apply any pending migrations."""
    before = None
    try:
        before = current_revision()
        upgrade_to_head()
    except (MigrationError, DatabaseError) as exc:
        typer.echo(f"Migration failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        logger.error("db_migrate_failed", error=str(exc))
        typer.echo(f"Migration failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    after = current_revision()
    if before == after:
        typer.echo(f"Already up to date at revision {after}")
    else:
        typer.echo(f"Migrated {before} -> {after}")


@app.command("status")
def status() -> None:
    """Show the applied revision versus the newest available revision."""
    try:
        applied = current_revision()
        head = head_revision()
    except Exception as exc:
        logger.error("db_status_failed", error=str(exc))
        typer.echo(f"Could not read migration status: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"applied: {applied}")
    typer.echo(f"head:    {head}")
    typer.echo("up to date" if applied == head else "PENDING MIGRATIONS")


@app.command("seed")
def seed() -> None:
    """Insert development reference data. Refuses to run outside the dev environment."""
    settings = get_settings()
    if settings.env != "dev":
        typer.echo(f"Refusing to seed in env '{settings.env}'; seeding is dev-only.", err=True)
        raise typer.Exit(code=1)

    try:
        with session_scope() as session:
            created = seed_dev_data(session)
    except AppError as exc:
        typer.echo(f"Seeding failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Seed complete; {created} row(s) created.")


@app.command("backup")
def backup(
    output_path: Path = typer.Argument(..., help="Destination file for the pg_dump archive."),
) -> None:
    """Write a pg_dump custom-format backup of the configured database."""
    settings = get_settings()
    url = urlparse(
        settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")
    )
    if not url.path or url.path == "/":
        typer.echo("Database URL has no database name; cannot back up.", err=True)
        raise typer.Exit(code=1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    logger.info("db_backup_start", target=str(output_path))

    env_password = url.password or ""
    cmd = [
        "pg_dump",
        "--format=custom",
        f"--file={output_path}",
        f"--host={url.hostname or 'localhost'}",
        f"--port={url.port or 5432}",
        f"--username={url.username or ''}",
        url.path.lstrip("/"),
    ]

    try:
        result = subprocess.run(
            cmd,
            env={"PGPASSWORD": env_password, "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except FileNotFoundError as exc:
        logger.error("db_backup_pg_dump_missing")
        typer.echo("pg_dump not found on PATH.", err=True)
        raise typer.Exit(code=1) from exc
    except subprocess.TimeoutExpired as exc:
        logger.error("db_backup_timeout")
        typer.echo("pg_dump timed out.", err=True)
        raise typer.Exit(code=1) from exc

    if result.returncode != 0:
        logger.error("db_backup_failed", returncode=result.returncode, stderr=result.stderr.strip())
        typer.echo(f"Backup failed: {result.stderr.strip()}", err=True)
        raise typer.Exit(code=1)

    elapsed = (datetime.now(UTC) - started).total_seconds()
    size = output_path.stat().st_size if output_path.exists() else 0
    logger.info("db_backup_complete", path=str(output_path), bytes=size, seconds=elapsed)
    typer.echo(f"Backup written to {output_path} ({size} bytes)")
