from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import structlog
import typer

from surplus_ai.database.engine import session_scope
from surplus_ai.database.models.enums import ReviewStatus
from surplus_ai.parser.document_classifier import DocumentClassifier
from surplus_ai.parser.evaluation import CorpusReport, Evaluator
from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.interpretation.county_config import CountyConfig, load_county_config
from surplus_ai.parser.interpretation.models import SurplusSource
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.models import ParsedDocumentResult
from surplus_ai.parser.persistence import DocumentPersister, ReviewQueue
from surplus_ai.parser.pipeline import ParsingPipeline
from surplus_ai.parser.profiles.learner import ProfileLearner
from surplus_ai.utils.exceptions import AppError

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


@app.command("ingest")
def ingest(
    pdf_path: Path = typer.Argument(..., help="PDF to parse, interpret and store."),
    state: str = typer.Option("", "--state", help="Two-letter state code."),
    county: str = typer.Option("", "--county", help="County slug, for its config file."),
) -> None:
    """Parse a PDF and store the result, queueing anything that needs a person.

    Re-running on the same file is a no-op: identity comes from the file's contents, not
    its name, because counties republish the same list under new filenames.
    """
    county_config = load_county_config(state, county) if state and county else None
    try:
        parsed = ParsingPipeline().parse(pdf_path)
        document = InterpretationPipeline().interpret(parsed, county_config)
    except ParserError as exc:
        typer.echo(f"Ingest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        with session_scope() as session:
            report = DocumentPersister(session).persist(parsed, document)
    except AppError as exc:
        typer.echo(f"Could not store the document: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if report.already_present:
        typer.echo(f"Already stored as document {report.document_id}; nothing was written.")
        return
    typer.echo(f"document:  {report.document_id}")
    typer.echo(f"rows:      {report.rows}")
    typer.echo(f"mappings:  {report.mappings}")
    typer.echo(f"to review: {report.queued_for_review}")


review_app = typer.Typer(help="Work the queue of rows that need a person.")
app.add_typer(review_app, name="review")


@review_app.command("list")
def review_list(
    limit: int = typer.Option(20, "--limit", help="How many items to show."),
) -> None:
    """Show pending rows, least confident first."""
    try:
        with session_scope() as session:
            items = ReviewQueue(session).pending(limit=limit)
            total = ReviewQueue(session).pending_count()
            rows = [
                (str(i.id), i.routing.value, i.confidence, i.page_number, i.reason) for i in items
            ]
    except AppError as exc:
        typer.echo(f"Could not read the review queue: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not rows:
        typer.echo("Nothing is waiting for review.")
        return
    typer.echo(f"{total} pending; showing {len(rows)}")
    for item_id, routing, confidence, page, reason in rows:
        typer.echo(f"  {item_id}  {routing:10} conf={confidence:.2f} p{page}  {reason[:90]}")


@review_app.command("resolve")
def review_resolve(
    item_id: str = typer.Argument(..., help="Review item id."),
    by: str = typer.Option(..., "--by", help="Who reviewed it."),
    notes: str = typer.Option("", "--notes", help="What was decided."),
    reject: bool = typer.Option(False, "--reject", help="Mark unusable instead of resolved."),
) -> None:
    """Close a queued row. The extracted row itself is never altered."""
    try:
        parsed_id = uuid.UUID(item_id)
    except ValueError as exc:
        typer.echo(f"{item_id!r} is not a valid id.", err=True)
        raise typer.Exit(code=1) from exc

    status = ReviewStatus.REJECTED if reject else ReviewStatus.RESOLVED
    try:
        with session_scope() as session:
            found = ReviewQueue(session).resolve(
                parsed_id, resolved_by=by, notes=notes, status=status
            )
    except AppError as exc:
        typer.echo(f"Could not resolve: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not found:
        typer.echo(f"No review item {item_id}.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Marked {status.value}.")


@app.command("evaluate")
def evaluate(
    corpus_dir: Path = typer.Option(
        Path("data/test_pdfs"), "--corpus", help="Directory of county PDFs."
    ),
    holdout_state: str = typer.Option("", "--state", help="State of the county to hold out."),
    holdout_county: str = typer.Option(
        "", "--holdout", help="County slug whose configuration to withhold."
    ),
    holdout_pdf: Path | None = typer.Option(
        None, "--holdout-pdf", help="The held-out county's PDF."
    ),
) -> None:
    """Measure how the pipeline performs, optionally on a county held out cold.

    Without a holdout this reports corpus-wide totals, so a change that helps one county
    while hurting the rest is visible. With one, it runs that county twice -- configured
    and cold -- to measure what the generic path achieves on a format it knows nothing
    about. That is the number that matters as counties are added, because a rule tuned
    until one county passes will pass that county forever.
    """
    evaluator = Evaluator()

    if holdout_county and holdout_pdf is not None:
        comparison = evaluator.compare_holdout(holdout_pdf, holdout_state, holdout_county)
        configured, cold = comparison.configured, comparison.cold_start
        typer.echo(f"county:            {comparison.county}")
        typer.echo(f"rows configured:   {configured.rows}")
        typer.echo(f"rows cold:         {cold.rows}")
        typer.echo(f"rows unchanged:    {comparison.rows_match}")
        typer.echo(
            f"columns resolved:  {configured.column_resolution_rate:.0%} configured, "
            f"{cold.column_resolution_rate:.0%} cold"
        )
        typer.echo(f"surplus configured: {configured.surplus_source.value}")
        typer.echo(f"surplus cold:       {cold.surplus_source.value}")
        typer.echo(f"degraded safely:    {comparison.surplus_degraded_safely}")
        if not comparison.rows_match:
            typer.echo(
                "\n  Extraction changed when configuration was withheld. County knowledge "
                "has leaked into extraction, which it must never do.",
                err=True,
            )
            raise typer.Exit(code=1)
        if not comparison.surplus_degraded_safely:
            typer.echo(
                "\n  The cold run named a surplus column the configured run did not. "
                "That is a guess made without the knowledge that settles it.",
                err=True,
            )
            raise typer.Exit(code=1)
        return

    pdfs = sorted(corpus_dir.glob("*.pdf"))
    if not pdfs:
        typer.echo(f"No PDFs found in {corpus_dir}.", err=True)
        raise typer.Exit(code=1)

    results = [evaluator.evaluate(pdf, _config_for(pdf)) for pdf in pdfs]
    report = CorpusReport(evaluations=tuple(results))

    typer.echo(f"counties:          {report.counties}")
    typer.echo(f"parsed:            {report.succeeded}")
    typer.echo(f"rows:              {report.total_rows}")
    typer.echo(f"column resolution: {report.mean_column_resolution:.1%}")
    typer.echo(f"naming a surplus:  {report.counties_naming_a_surplus}")
    typer.echo("")
    for result in results:
        if not result.succeeded:
            typer.echo(f"  {result.county:26} FAILED  {result.error}")
            continue
        typer.echo(
            f"  {result.county:26} rows={result.rows:5} "
            f"columns={result.column_resolution_rate:5.0%} "
            f"surplus={result.surplus_source.value}"
        )


def _config_for(pdf: Path) -> CountyConfig | None:
    """Load a corpus file's county configuration by convention, if one exists."""
    for state, slug in _CORPUS_COUNTY_CONFIGS.items():
        if pdf.stem.lower().startswith(slug.replace("-", "_")):
            return load_county_config(state, slug)
    return None


# Corpus files are named <County>_County_<STATE>[_<year>].pdf; this maps the ones that
# ship a configuration. Production ingestion passes the county explicitly instead.
_CORPUS_COUNTY_CONFIGS: dict[str, str] = {"in": "marion"}
