from __future__ import annotations

import uuid

import structlog
import typer

from surplus_ai.database.engine import session_scope
from surplus_ai.research.exceptions import ResearchError
from surplus_ai.research.pipeline import ResearchPipeline
from surplus_ai.research.registry import ProviderRegistry
from surplus_ai.utils.exceptions import AppError

logger = structlog.get_logger(__name__)

app = typer.Typer(help="Property-record research (Phase 6A: ResearchResult persistence only).")


@app.command("providers")
def list_providers() -> None:
    """List configured property providers and credential status (no network calls)."""
    try:
        registry = ProviderRegistry()
    except ResearchError as exc:
        typer.echo(f"Could not load research providers: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    rows = registry.describe()
    if not rows:
        typer.echo("No providers configured.")
        return

    typer.echo(f"default: {registry.default_provider_name}")
    typer.echo("")
    for row in rows:
        default_mark = " (default)" if row["is_default"] else ""
        cred = ""
        if row["credential_env"]:
            cred = f"  cred={row['credential_env']}:{row['credential_status']}"
        typer.echo(f"  {row['name']:24} type={row['type']:20}{default_mark}{cred}")
        if row["description"]:
            typer.echo(f"    {row['description']}")


@app.command("case")
def research_case(
    case_id: str = typer.Argument(..., help="Surplus case UUID."),
) -> None:
    """Run property research for one case and append a ResearchResult row."""
    try:
        cid = uuid.UUID(case_id)
    except ValueError as exc:
        typer.echo(f"Invalid case id: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        with session_scope() as session:
            row = ResearchPipeline(session).research_case(cid)
            status = row.status.value
            provider = row.provider
            result_id = row.id
            payload = row.response_payload or {}
    except (AppError, ResearchError) as exc:
        typer.echo(f"Research failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"research_result: {result_id}")
    typer.echo(f"provider:        {provider}")
    typer.echo(f"status:          {status}")
    typer.echo(f"found:           {payload.get('found')}")
    typer.echo(f"human_review:    {payload.get('requires_human_review')}")
    if payload.get("notes"):
        typer.echo(f"notes:           {payload.get('notes')}")


@app.command("pending")
def research_pending(
    state: str | None = typer.Option(None, "--state", help="Two-letter state filter."),
    county: str | None = typer.Option(None, "--county", help="County slug filter."),
    limit: int = typer.Option(100, "--limit", min=1, help="Max cases to research."),
) -> None:
    """Research pending candidates; appends one ResearchResult per case."""
    try:
        with session_scope() as session:
            summary, _rows = ResearchPipeline(session).research_pending(
                state=state, county_slug=county, limit=limit
            )
            lines = summary.summary_lines()
    except (AppError, ResearchError) as exc:
        typer.echo(f"Research failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for line in lines:
        typer.echo(line)


@app.command("status")
def research_status(
    state: str | None = typer.Option(None, "--state", help="Two-letter state filter."),
    county: str | None = typer.Option(None, "--county", help="County slug filter."),
    case_id: str | None = typer.Option(None, "--case-id", help="Limit to one case UUID."),
    limit: int = typer.Option(50, "--limit", min=1, help="Max rows to show."),
) -> None:
    """Show recent ResearchResult rows (read-only)."""
    cid: uuid.UUID | None = None
    if case_id:
        try:
            cid = uuid.UUID(case_id)
        except ValueError as exc:
            typer.echo(f"Invalid case id: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    try:
        with session_scope() as session:
            rows = ResearchPipeline(session).status_rows(
                state=state, county_slug=county, case_id=cid, limit=limit
            )
            display = [
                (
                    str(r.id),
                    str(r.surplus_case_id),
                    r.provider,
                    r.status.value,
                    r.fetched_at.isoformat() if r.fetched_at else "",
                    (r.response_payload or {}).get("requires_human_review"),
                )
                for r in rows
            ]
    except (AppError, ResearchError) as exc:
        typer.echo(f"Could not read research status: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not display:
        typer.echo("No research results found.")
        return

    typer.echo(f"{len(display)} result(s)")
    for rid, case, provider, status, fetched, review in display:
        typer.echo(
            f"  {rid}  case={case}  provider={provider}  status={status}  "
            f"review={review}  at={fetched}"
        )
