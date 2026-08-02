from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
import typer

from surplus_ai.parser.document_classifier import DocumentClassifier
from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.interpretation.county_config import load_county_config
from surplus_ai.parser.interpretation.models import SurplusSource
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.models import ParsedDocumentResult
from surplus_ai.parser.pipeline import ParsingPipeline
from surplus_ai.parser.profiles.learner import ProfileLearner

logger = structlog.get_logger(__name__)

app = typer.Typer(help="PDF parsing and county onboarding commands.")


@app.command("classify")
def classify(
    pdf_path: Path = typer.Argument(..., help="PDF to profile."),
) -> None:
    """Report how a PDF stores its data and whether OCR is needed."""
    try:
        profile = DocumentClassifier().classify(pdf_path)
    except ParserError as exc:
        typer.echo(f"Could not classify {pdf_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"file:          {profile.source_path}")
    typer.echo(f"sha256:        {profile.source_sha256[:16]}...")
    typer.echo(f"pages:         {profile.page_count}")
    typer.echo(f"pdf_type:      {profile.pdf_type.value}")
    typer.echo(f"ocr_required:  {profile.ocr_required}")
    if profile.ocr_required:
        typer.echo(f"ocr_pages:     {list(profile.ocr_required_pages)}")
    typer.echo(f"confidence:    {profile.confidence:.2f}")


@app.command("inspect")
def inspect(
    pdf_path: Path = typer.Argument(..., help="PDF to parse."),
    show_rows: int = typer.Option(3, "--rows", help="Sample rows to print per table."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
) -> None:
    """Parse a PDF and show what was extracted, without writing anything."""
    try:
        result = ParsingPipeline().parse(pdf_path)
    except ParserError as exc:
        typer.echo(f"Parse failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(_to_dict(result), indent=2, default=str))
        return

    typer.echo(f"file:        {result.profile.source_path}")
    typer.echo(f"pdf_type:    {result.profile.pdf_type.value}")
    typer.echo(f"strategy:    {result.winning_strategy}")
    typer.echo(f"scores:      {_format_scores(result.strategy_scores)}")
    typer.echo(f"confidence:  {result.extraction_confidence:.3f}")
    typer.echo(f"tables:      {len(result.tables)}")
    typer.echo(f"rows:        {result.total_rows}")
    typer.echo(f"fragments:   {len(result.unparsed_fragments)}")

    for table in result.tables:
        typer.echo("")
        typer.echo(
            f"  table {table.table_index}: {table.row_count} rows "
            f"on pages {list(table.page_numbers)}"
        )
        typer.echo(f"  headers: {list(table.original_headers)}")
        for row in table.rows[:show_rows]:
            typer.echo(f"    p{row.page_number} {json.dumps(row.values, ensure_ascii=False)[:160]}")

    for warning in result.warnings:
        typer.echo(f"  warning: {warning}", err=True)


@app.command("interpret")
def interpret(
    pdf_path: Path = typer.Argument(..., help="PDF to parse and interpret."),
    state: str = typer.Option(
        "", "--state", help="Two-letter state code, to load this county's config."
    ),
    county: str = typer.Option("", "--county", help="County slug, to load this county's config."),
    show_rows: int = typer.Option(3, "--rows", help="Sample rows to print per table."),
) -> None:
    """Parse a PDF and map its columns onto the universal schema.

    Reports what each published column became, whether the county publishes a claimable
    surplus, and how many rows still have money outstanding.
    """
    county_config = None
    if state and county:
        county_config = load_county_config(state, county)
        if county_config is None:
            typer.echo(
                f"No configuration found for {county}, {state}; using the generic rules.",
                err=True,
            )

    try:
        parsed = ParsingPipeline().parse(pdf_path)
        document = InterpretationPipeline().interpret(parsed, county_config)
    except ParserError as exc:
        typer.echo(f"Interpretation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"file:          {document.source_path}")
    typer.echo(f"county config: {document.county_config_used or '(none)'}")
    typer.echo(f"rows:          {document.total_rows}")
    typer.echo(f"with a figure: {document.rows_with_surplus}")
    typer.echo(f"claimable:     {document.rows_with_claimable_surplus}")
    routing = ", ".join(f"{k.value}={v}" for k, v in document.routing_counts().items() if v)
    typer.echo(f"routing:       {routing}")

    for table in document.tables:
        surplus = table.surplus
        typer.echo("")
        typer.echo(f"  table {table.table_index}: {len(table.rows)} rows")
        typer.echo(f"  surplus:  {surplus.source.value}", nl=False)
        typer.echo(f" from {surplus.source_column!r}" if surplus.source_column else "")
        typer.echo(f"            {surplus.reason}")
        typer.echo("  columns:")
        for mapping in table.mappings:
            target = mapping.canonical_field.value if mapping.canonical_field else "(unresolved)"
            typer.echo(
                f"    {mapping.original_header:34} -> {target:22} "
                f"{mapping.method.value:17} {mapping.confidence:.2f}"
            )
        for row in table.rows[:show_rows]:
            typer.echo(
                f"    p{row.page_number} surplus={row.surplus_amount} "
                f"routing={row.routing.value} confidence={row.confidence:.3f}"
            )

    if any(t.surplus.source is SurplusSource.AMBIGUOUS for t in document.tables):
        typer.echo(
            "\n  Set surplus_column in this county's config file to resolve the ambiguity.",
            err=True,
        )


def _format_scores(scores: dict[str, float]) -> str:
    return ", ".join(f"{name}={value:.3f}" for name, value in sorted(scores.items()))


def _to_dict(result: ParsedDocumentResult) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(result.model_dump_json())
    return parsed


@app.command("profile")
def profile(
    pdf_path: Path = typer.Argument(..., help="PDF to learn a county profile from."),
    slug: str = typer.Option(..., "--slug", help="County slug the profile belongs to."),
    state: str = typer.Option("", "--state", help="Two-letter state code."),
    county: str = typer.Option("", "--county", help="County slug for its config file."),
) -> None:
    """Learn what a document reveals about how its county publishes lists.

    Prints the profile and its version fingerprint without writing anything. Two documents
    in the same layout produce the same fingerprint even when their record counts differ,
    which is how a second file is recognised as another sighting rather than a new layout.
    """
    county_config = load_county_config(state, county) if state and county else None
    try:
        parsed = ParsingPipeline().parse(pdf_path)
        interpreted = InterpretationPipeline().interpret(parsed, county_config)
    except ParserError as exc:
        typer.echo(f"Could not profile {pdf_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    learned = ProfileLearner().learn(
        slug, parsed, interpreted, county_name=county or slug, state=state
    )
    owners = learned.owner_types

    typer.echo(f"county:            {learned.county_name} ({learned.state or 'unknown'})")
    typer.echo(f"version:           {learned.version_hash()}")
    typer.echo(f"pdf type:          {learned.pdf_type.value}")
    typer.echo(f"ocr required:      {learned.ocr_required}")
    typer.echo(f"parsing strategy:  {learned.required_parsing_strategy}")
    typer.echo(f"tables:            {len(learned.table_structures)}")
    typer.echo(f"typical rows:      {learned.typical_row_count}")
    typer.echo(f"surplus listed:    {learned.surplus_explicitly_listed}")
    typer.echo(f"surplus source:    {learned.surplus_source.value}")
    if learned.surplus_source_column:
        typer.echo(f"surplus column:    {learned.surplus_source_column!r}")
    typer.echo(f"columns:           {list(learned.original_column_names)}")
    if learned.unresolved_columns:
        typer.echo(f"unresolved:        {list(learned.unresolved_columns)}")
    if owners.sampled:
        typer.echo(
            f"owner names:       {owners.sampled} sampled, "
            f"{owners.entity_share:.0%} look like entities"
        )
