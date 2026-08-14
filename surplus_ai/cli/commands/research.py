from __future__ import annotations

import uuid

import structlog
import typer

from surplus_ai.database.engine import session_scope
from surplus_ai.database.models.enums import (
    ResearchReviewReason,
    ResearchReviewResolution,
    ReviewStatus,
)
from surplus_ai.research.exceptions import ResearchError, ResearchReviewError
from surplus_ai.research.pipeline import ResearchPipeline
from surplus_ai.research.registry import ProviderRegistry
from surplus_ai.research.review import ResearchReviewQueue
from surplus_ai.utils.exceptions import AppError

logger = structlog.get_logger(__name__)

app = typer.Typer(help="Property-record research (Phase 6C: cache, pacing, retries, review queue).")
review_app = typer.Typer(help="Human research review queue (evidence/workflow only).")
app.add_typer(review_app, name="review")


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
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass cache reads; still call the provider and persist the new result.",
    ),
) -> None:
    """Run property research for one case and append a ResearchResult row."""
    try:
        cid = uuid.UUID(case_id)
    except ValueError as exc:
        typer.echo(f"Invalid case id: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        with session_scope() as session:
            row = ResearchPipeline(session).research_case(cid, use_cache=not no_cache)
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
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass cache reads; still call the provider and persist each new result.",
    ),
) -> None:
    """Research pending candidates; appends one ResearchResult per newly obtained outcome.

    Candidate selection is unchanged from Phase 6A (not "unresearched only").
    """
    try:
        with session_scope() as session:
            summary, _rows = ResearchPipeline(session).research_pending(
                state=state, county_slug=county, limit=limit, use_cache=not no_cache
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


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        typer.echo(f"Invalid {label}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _parse_review_status(value: str) -> ReviewStatus | None:
    normalized = value.strip().lower()
    if normalized == "all":
        return None
    try:
        return ReviewStatus(normalized)
    except ValueError as exc:
        typer.echo(
            "Invalid --status. Use pending, resolved, rejected, or all.",
            err=True,
        )
        raise typer.Exit(code=1) from exc


def _parse_reason(value: str | None) -> ResearchReviewReason | None:
    if value is None:
        return None
    try:
        return ResearchReviewReason(value.strip().lower())
    except ValueError as exc:
        typer.echo(f"Invalid --reason: {value}", err=True)
        raise typer.Exit(code=1) from exc


def _parse_resolution(value: str | None) -> ResearchReviewResolution | None:
    if value is None:
        return None
    try:
        return ResearchReviewResolution(value.strip().lower())
    except ValueError as exc:
        typer.echo(f"Invalid --resolution: {value}", err=True)
        raise typer.Exit(code=1) from exc


@review_app.command("list")
def review_list(
    status: str = typer.Option("pending", "--status", help="pending|resolved|rejected|all."),
    state: str | None = typer.Option(None, "--state", help="Two-letter state filter."),
    county: str | None = typer.Option(None, "--county", help="County slug filter."),
    provider: str | None = typer.Option(None, "--provider", help="Provider name filter."),
    reason: str | None = typer.Option(None, "--reason", help="Review reason filter."),
    case_id: str | None = typer.Option(None, "--case-id", help="Limit to one case UUID."),
    limit: int = typer.Option(20, "--limit", min=1, help="Max items to show."),
) -> None:
    """List research review items. Default is pending."""
    parsed_status = _parse_review_status(status)
    parsed_reason = _parse_reason(reason)
    cid = _parse_uuid(case_id, "case id") if case_id else None
    try:
        with session_scope() as session:
            total, items = ResearchReviewQueue(session).list_items(
                status=parsed_status,
                state=state,
                county_slug=county,
                provider=provider,
                reason=parsed_reason,
                case_id=cid,
                limit=limit,
            )
            rows = [
                (
                    str(item.id),
                    item.status.value,
                    item.reason.value,
                    item.provider,
                    str(item.surplus_case_id),
                    item.created_at.isoformat() if item.created_at else "",
                )
                for item in items
            ]
    except (AppError, ResearchError) as exc:
        typer.echo(f"Could not read research review queue: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not rows:
        typer.echo("Nothing is waiting for research review.")
        return
    typer.echo(f"{total} matching; showing {len(rows)}")
    for item_id, item_status, item_reason, item_provider, case, created in rows:
        typer.echo(
            f"  {item_id}  {item_status:9}  {item_reason:24}  "
            f"provider={item_provider}  case={case}  at={created}"
        )


@review_app.command("show")
def review_show(item_id: str = typer.Argument(..., help="Review item UUID.")) -> None:
    """Show one review item and a triggering-result summary. Does not dump payloads."""
    parsed_id = _parse_uuid(item_id, "review item id")
    try:
        with session_scope() as session:
            shown = ResearchReviewQueue(session).show(parsed_id)
            item = shown.item
            triggering = shown.triggering
            newer = shown.newer
            payload = triggering.response_payload or {}
            lines = [
                f"review_item:          {item.id}",
                f"status:               {item.status.value}",
                f"reason:               {item.reason.value}",
                f"reason_detail:        {item.reason_detail or ''}",
                f"provider:             {item.provider}",
                f"case:                 {item.surplus_case_id}",
                f"resolution:           {item.resolution.value if item.resolution else ''}",
                f"reviewed_by:          {item.reviewed_by or ''}",
                f"reviewed_at:          {item.reviewed_at.isoformat() if item.reviewed_at else ''}",
                f"reviewer_notes:       {item.reviewer_notes or ''}",
                f"triggering_result:    {triggering.id}",
                f"triggering_status:    {triggering.status.value}",
                (
                    "triggering_fetched:   "
                    f"{triggering.fetched_at.isoformat() if triggering.fetched_at else ''}"
                ),
                f"human_review_flag:    {payload.get('requires_human_review')}",
                f"provider_status:      {payload.get('provider_status')}",
                f"error_code:           {payload.get('error_code')}",
            ]
            if newer is None:
                lines.append("Newer research available: no")
            else:
                lines.extend(
                    [
                        "Newer research available: yes",
                        f"Latest research result: {newer.id}",
                        "Latest fetched_at: "
                        f"{newer.fetched_at.isoformat() if newer.fetched_at else ''}",
                        f"Latest status: {newer.status.value}",
                    ]
                )
    except ResearchReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (AppError, ResearchError) as exc:
        typer.echo(f"Could not show research review item: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for line in lines:
        typer.echo(line)


@review_app.command("resolve")
def review_resolve(
    item_id: str = typer.Argument(..., help="Review item UUID."),
    by: str = typer.Option(..., "--by", help="Who reviewed it."),
    notes: str = typer.Option("", "--notes", help="Reviewer notes."),
    resolution: str | None = typer.Option(
        None,
        "--resolution",
        help="evidence_usable|evidence_insufficient|needs_additional_research|conflict_unresolved.",
    ),
    reject: bool = typer.Option(False, "--reject", help="Mark not_relevant instead of resolved."),
) -> None:
    """Close a pending review item. Does not mutate ResearchResult or authorize contact."""
    parsed_id = _parse_uuid(item_id, "review item id")
    parsed_resolution = _parse_resolution(resolution)
    try:
        with session_scope() as session:
            closed = ResearchReviewQueue(session).resolve(
                parsed_id,
                reviewed_by=by,
                notes=notes,
                reject=reject,
                resolution=parsed_resolution,
            )
            status = closed.status.value
            recorded = closed.resolution.value if closed.resolution else ""
    except ResearchReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (AppError, ResearchError) as exc:
        typer.echo(f"Could not resolve research review item: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Marked {status} ({recorded}).")
