from __future__ import annotations

from datetime import date
from decimal import Decimal

import structlog
import typer

from surplus_ai.compliance.engine import ComplianceCase, ComplianceEngine
from surplus_ai.compliance.exceptions import ComplianceError
from surplus_ai.compliance.rules_loader import STATES_CONFIG_DIR, ComplianceRulesLoader

logger = structlog.get_logger(__name__)

app = typer.Typer(help="State compliance rules and eligibility checks.")


@app.command("states")
def list_states() -> None:
    """Show which states have rules, and which of those can actually be relied on."""
    try:
        rules = ComplianceRulesLoader().load_all()
    except ComplianceError as exc:
        typer.echo(f"Could not read the state rules: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not rules:
        typer.echo(f"No state rules found in {STATES_CONFIG_DIR}.")
        return

    usable = [code for code, r in rules.items() if r.is_usable]
    typer.echo(f"{len(rules)} state file(s); {len(usable)} usable")
    typer.echo("")
    for code, entry in sorted(rules.items()):
        status = "usable" if entry.is_usable else "BLOCKED"
        typer.echo(f"  {code}  {status:8} {entry.state_name}")
        if not entry.is_usable:
            typer.echo(f"       missing: {list(entry.missing_fields())}")

    if not usable:
        typer.echo(
            "\n  No state can currently clear a case. Every case will be reported "
            "ineligible until a state's statutory values are recorded and verified.",
            err=True,
        )


@app.command("validate")
def validate(
    state: str = typer.Argument(..., help="Two-letter state code."),
) -> None:
    """Report exactly what a state's rules still need before they can be used."""
    entry = ComplianceRulesLoader().load_or_none(state)
    if entry is None:
        typer.echo(
            f"No rules file for {state.upper()}. Copy "
            f"{STATES_CONFIG_DIR / '_template.yaml'} to "
            f"{STATES_CONFIG_DIR / (state.lower() + '.yaml')} and fill it in.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"state:     {entry.state_code} {entry.state_name}")
    typer.echo(f"verified:  {entry.verified}")
    typer.echo(f"usable:    {entry.is_usable}")
    typer.echo(f"citations: {list(entry.statute_citations) or '(none)'}")
    if entry.is_usable:
        typer.echo(f"wait:      {entry.waiting_period_days} day(s) after the sale")
        typer.echo(f"fee cap:   {entry.fee_cap_basis.value} {entry.max_contingency_fee_pct}")
        return

    typer.echo(f"\nmissing:   {list(entry.missing_fields())}")
    typer.echo(
        "\nUntil these are recorded from the statute, every case in this state is "
        "reported ineligible. That is deliberate — a guessed value would clear cases "
        "against a rule nobody checked."
    )
    raise typer.Exit(code=1)


@app.command("check")
def check(
    state: str = typer.Argument(..., help="Two-letter state code."),
    sale_date: str = typer.Option("", "--sale-date", help="Sale date, YYYY-MM-DD."),
    amount: str = typer.Option("", "--amount", help="Surplus amount, to quote a fee cap."),
    as_of: str = typer.Option("", "--as-of", help="Evaluate as of this date, YYYY-MM-DD."),
) -> None:
    """Decide whether a case may be worked, and say why when it may not."""
    try:
        parsed_sale = date.fromisoformat(sale_date) if sale_date else None
        parsed_as_of = date.fromisoformat(as_of) if as_of else None
    except ValueError as exc:
        typer.echo(f"Dates must be YYYY-MM-DD: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    case = ComplianceCase(
        state_code=state.upper(),
        sale_date=parsed_sale,
        surplus_amount=Decimal(amount) if amount else None,
    )
    result = ComplianceEngine().evaluate(case, as_of=parsed_as_of)

    typer.echo(f"state:     {result.state_code}")
    typer.echo(f"eligible:  {result.is_eligible}")
    typer.echo(f"verified:  {result.rules_verified}")
    if result.earliest_contact_date:
        typer.echo(f"contact:   from {result.earliest_contact_date.isoformat()}")
    if result.maximum_fee is not None:
        typer.echo(f"max fee:   {result.maximum_fee}")
    for requirement in result.contract_requirements:
        typer.echo(f"  - {requirement}")
    for disclosure in result.disclosures_required:
        typer.echo(f"  disclosure: {disclosure}")

    if result.blocking_reasons:
        typer.echo("")
        for reason in result.blocking_reasons:
            typer.echo(f"  [{reason.code.value}] {reason.detail}", err=True)
        raise typer.Exit(code=1)
