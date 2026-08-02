from __future__ import annotations

from pathlib import Path

import structlog
import typer

from surplus_ai.classifier.exceptions import ClassificationError
from surplus_ai.classifier.owner_type_classifier import (
    ClassificationSummary,
    OwnerTypeClassifier,
)
from surplus_ai.database.models.enums import OwnerType
from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.county_config import load_county_config
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.pipeline import ParsingPipeline

logger = structlog.get_logger(__name__)

app = typer.Typer(help="Owner classification commands.")


@app.command("name")
def classify_name(
    owner_name: str = typer.Argument(..., help="An owner name to classify."),
) -> None:
    """Classify a single owner name and explain the verdict."""
    try:
        result = OwnerTypeClassifier().classify(owner_name)
    except ClassificationError as exc:
        typer.echo(f"Could not classify: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"name:       {result.raw_name}")
    typer.echo(f"type:       {result.owner_type.value}")
    typer.echo(f"confidence: {result.confidence:.2f}")
    typer.echo(f"pursue:     {result.is_pursuable}")
    typer.echo(f"why:        {result.evidence}")
    if result.person_name and result.person_name.is_split:
        person = result.person_name
        typer.echo(f"parsed:     last={person.last!r} first={person.first!r}")
        if person.additional_parties:
            typer.echo(f"also owed:  {list(person.additional_parties)}")


@app.command("document")
def classify_document(
    pdf_path: Path = typer.Argument(..., help="PDF whose owners should be classified."),
    state: str = typer.Option("", "--state", help="Two-letter state code."),
    county: str = typer.Option("", "--county", help="County slug, for its config file."),
    show: int = typer.Option(0, "--show", help="Print this many classified names."),
) -> None:
    """Classify every owner in a county list and report what it contains.

    Companies are excluded from the pursuable set and individuals kept, as the brief asks.
    Estates and trusts are kept too and reported separately: an estate is a lead, not a
    company, and its heirs are entitled to the money.
    """
    county_config = load_county_config(state, county) if state and county else None
    try:
        parsed = ParsingPipeline().parse(pdf_path)
        document = InterpretationPipeline().interpret(parsed, county_config)
    except ParserError as exc:
        typer.echo(f"Could not read {pdf_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    names = [
        value
        for table in document.tables
        for row in table.rows
        if isinstance(value := row.value(CanonicalField.OWNER_NAME), str) and value.strip()
    ]
    if not names:
        typer.echo("No owner-name column was resolved in this document.", err=True)
        raise typer.Exit(code=1)

    summary = ClassificationSummary(OwnerTypeClassifier().classify_many(names))

    typer.echo(f"file:      {pdf_path}")
    typer.echo(f"owners:    {summary.total}")
    typer.echo(f"pursue:    {len(summary.pursuable)}")
    typer.echo(f"exclude:   {len(summary.excluded)}")
    typer.echo("")
    for owner_type in OwnerType:
        count = summary.counts.get(owner_type, 0)
        if count:
            typer.echo(f"  {owner_type.value:11} {count:5}  {summary.share(owner_type):5.1%}")

    for result in summary.results[:show]:
        typer.echo(
            f"    {result.raw_name[:44]:44} {result.owner_type.value:11} "
            f"{result.confidence:.2f} pursue={result.is_pursuable}"
        )
