from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
import typer

from surplus_ai.parser.document_classifier import DocumentClassifier
from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.models import ParsedDocumentResult
from surplus_ai.parser.pipeline import ParsingPipeline

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


def _format_scores(scores: dict[str, float]) -> str:
    return ", ".join(f"{name}={value:.3f}" for name, value in sorted(scores.items()))


def _to_dict(result: ParsedDocumentResult) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(result.model_dump_json())
    return parsed
