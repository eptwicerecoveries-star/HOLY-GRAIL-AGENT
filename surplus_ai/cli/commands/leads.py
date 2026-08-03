from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog
import typer

from surplus_ai.database.engine import session_scope
from surplus_ai.leads.county_registry import CountyRegistry
from surplus_ai.leads.exceptions import LeadError
from surplus_ai.leads.pipeline import LeadInventory, LeadPipeline
from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.interpretation.county_config import load_county_config
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.persistence import DocumentPersister
from surplus_ai.parser.pipeline import ParsingPipeline
from surplus_ai.utils.exceptions import AppError

logger = structlog.get_logger(__name__)

app = typer.Typer(help="Turn parsed county documents into cases, owners and leads.")


@app.command("build")
def build(
    pdf_path: Path = typer.Argument(..., help="County PDF to process end to end."),
    state: str = typer.Option(..., "--state", help="Two-letter state code."),
    county: str = typer.Option(..., "--county", help="County slug."),
    as_of: str = typer.Option("", "--as-of", help="Evaluate compliance as of YYYY-MM-DD."),
) -> None:
    """Parse, interpret, store, and build cases, owners and leads from one document.

    Re-running on the same file is safe: cases are matched on what the county published,
    so a republished list updates the same records rather than doubling them.
    """
    try:
        evaluated_on = date.fromisoformat(as_of) if as_of else None
    except ValueError as exc:
        typer.echo(f"--as-of must be YYYY-MM-DD: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    county_config = load_county_config(state, county)
    try:
        parsed = ParsingPipeline().parse(pdf_path)
        interpreted = InterpretationPipeline().interpret(parsed, county_config)
    except ParserError as exc:
        typer.echo(f"Could not read {pdf_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        with session_scope() as session:
            stored = DocumentPersister(session).persist(parsed, interpreted)
            report = LeadPipeline(session).build(
                interpreted,
                state=state,
                county_slug=county,
                parsed_document_id=stored.document_id,
                as_of=evaluated_on,
            )
            lines = report.summary_lines()
    except (AppError, LeadError) as exc:
        typer.echo(f"Could not build leads: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"document:  {stored.document_id}")
    for line in lines:
        typer.echo(line)
    if report.leads_total == 0:
        typer.echo(
            "\n  No leads. Check the reasons above: a compliance_blocked count equal to "
            "the claimable cases means the state's statutes have not been recorded yet, "
            "which holds every case in that state. See "
            "docs/runbooks/onboarding_a_state_compliance_profile.md.",
            err=True,
        )


@app.command("promote")
def promote(
    state: str = typer.Option("", "--state", help="Limit to one state."),
    county: str = typer.Option("", "--county", help="Limit to one county slug."),
    as_of: str = typer.Option("", "--as-of", help="Evaluate compliance as of YYYY-MM-DD."),
) -> None:
    """Re-check stored cases and create leads for any that now qualify.

    Run this after recording a state's statutes: cases held only by an unverified rule set
    become leads without re-parsing a single PDF.
    """
    try:
        evaluated_on = date.fromisoformat(as_of) if as_of else None
    except ValueError as exc:
        typer.echo(f"--as-of must be YYYY-MM-DD: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        with session_scope() as session:
            report = LeadPipeline(session).promote(
                state=state, county_slug=county, as_of=evaluated_on
            )
            lines = report.summary_lines()
    except (AppError, LeadError) as exc:
        typer.echo(f"Could not promote cases: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for line in lines:
        typer.echo(line)


@app.command("status")
def status(
    state: str = typer.Option("", "--state", help="Limit to one state."),
    county: str = typer.Option("", "--county", help="Limit to one county slug."),
) -> None:
    """Report how many cases exist, how many hold money, and how many became leads."""
    try:
        with session_scope() as session:
            county_id = None
            if state and county:
                found = CountyRegistry(session).find(state, county)
                if found is None:
                    typer.echo(f"No county registered as {state.upper()}/{county}.", err=True)
                    raise typer.Exit(code=1)
                county_id = found.id
            inventory = LeadInventory(session)
            cases = inventory.case_count(county_id)
            claimable = inventory.claimable_case_count(county_id)
            leads = inventory.lead_count(county_id)
    except (AppError, LeadError) as exc:
        typer.echo(f"Could not read the pipeline: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"cases:     {cases}")
    typer.echo(f"claimable: {claimable}")
    typer.echo(f"leads:     {leads}")
    if claimable and not leads:
        typer.echo(
            "\n  Money is recorded but nothing is a lead. Every case is held somewhere; "
            "`surplusai compliance states` will say whether the state rules are the cause.",
            err=True,
        )
