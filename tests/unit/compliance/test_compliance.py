"""Compliance rules and eligibility.

Every test here exists because the two directions of error are not comparable. Wrongly
holding a case back costs one lead and shows up in the blocking counts. Wrongly releasing
one can mean contacting a former owner during a statutory blackout or agreeing a fee above
a state's cap — which voids contracts and, in several states, is a criminal matter.

So the suite is weighted towards proving the system refuses rather than proving it permits.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from surplus_ai.cli.main import build_app
from surplus_ai.compliance.disclosure import contract_requirements, required_disclosures
from surplus_ai.compliance.engine import (
    BlockingReasonCode,
    ComplianceCase,
    ComplianceEngine,
)
from surplus_ai.compliance.exceptions import (
    ComplianceConfigError,
    ComplianceError,
    StateRulesNotFoundError,
)
from surplus_ai.compliance.fee_cap import fee_is_permitted, maximum_fee_for, validate_fee_pct
from surplus_ai.compliance.rules_loader import ComplianceRulesLoader
from surplus_ai.compliance.state_rules import FeeCapBasis, StateComplianceRules
from surplus_ai.compliance.waiting_period import (
    compute_earliest_contact_date,
    days_until_contact_allowed,
    waiting_period_has_elapsed,
)
from surplus_ai.utils.exceptions import AppError

TODAY = date(2026, 6, 1)


def _rules(**overrides: object) -> StateComplianceRules:
    """A fully verified rule set, used to test the permissive path deliberately."""
    defaults: dict[str, object] = {
        "state_code": "ZZ",
        "state_name": "Testland",
        "verified": True,
        "statute_citations": ("Test Code § 1-100",),
        "waiting_period_days": 90,
        "fee_cap_basis": FeeCapBasis.PERCENTAGE_OF_RECOVERY,
        "max_contingency_fee_pct": 20.0,
    }
    defaults.update(overrides)
    return StateComplianceRules(**defaults)  # type: ignore[arg-type]


def _case(**overrides: object) -> ComplianceCase:
    defaults: dict[str, object] = {"state_code": "ZZ", "sale_date": date(2025, 1, 1)}
    defaults.update(overrides)
    return ComplianceCase(**defaults)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def engine() -> ComplianceEngine:
    return ComplianceEngine()


# --------------------------------------------------------------------------------------
# Failing closed
# --------------------------------------------------------------------------------------


def test_a_state_with_no_rules_blocks_every_case(engine: ComplianceEngine) -> None:
    """An unresearched state must hold its cases, not release them."""
    result = engine.evaluate(ComplianceCase(state_code="QQ", sale_date=date(2020, 1, 1)))

    assert result.is_eligible is False
    assert BlockingReasonCode.NO_STATE_RULES in result.blocking_codes


def test_unverified_rules_block_every_case(engine: ComplianceEngine) -> None:
    """A file can exist, be committed, and still not be trusted."""
    result = engine.evaluate(_case(), rules=_rules(verified=False), as_of=TODAY)

    assert result.is_eligible is False
    assert BlockingReasonCode.RULES_UNVERIFIED in result.blocking_codes


def test_verified_but_incomplete_rules_block(engine: ComplianceEngine) -> None:
    """Verification is not a rubber stamp: the values still have to be there."""
    incomplete = StateComplianceRules(
        state_code="ZZ", verified=True, statute_citations=("x",), waiting_period_days=None
    )

    result = engine.evaluate(_case(), rules=incomplete, as_of=TODAY)

    assert result.is_eligible is False
    assert BlockingReasonCode.RULES_INCOMPLETE in result.blocking_codes


def test_a_case_with_no_sale_date_cannot_be_cleared(engine: ComplianceEngine) -> None:
    """The waiting period has to count from something."""
    result = engine.evaluate(_case(sale_date=None), rules=_rules(), as_of=TODAY)

    assert result.is_eligible is False
    assert BlockingReasonCode.NO_SALE_DATE in result.blocking_codes


def test_eligibility_is_never_the_default() -> None:
    """The model itself defaults to ineligible, so a construction bug fails safe."""
    from surplus_ai.compliance.engine import ComplianceResult

    assert ComplianceResult(state_code="ZZ").is_eligible is False


@pytest.mark.parametrize(
    "missing",
    [
        {"verified": False},
        {"waiting_period_days": None},
        {"max_contingency_fee_pct": None},
        {"statute_citations": ()},
    ],
)
def test_each_required_fact_is_individually_required(missing: dict[str, object]) -> None:
    assert _rules(**missing).is_usable is False


def test_a_state_that_caps_nothing_is_a_positive_finding() -> None:
    """ "No cap" and "cap unknown" are opposite conclusions and must not be conflated."""
    capped_nothing = _rules(fee_cap_basis=FeeCapBasis.NONE, max_contingency_fee_pct=None)
    unknown = _rules(max_contingency_fee_pct=None)

    assert capped_nothing.is_usable is True
    assert unknown.is_usable is False


# --------------------------------------------------------------------------------------
# The permissive path, once everything is known
# --------------------------------------------------------------------------------------


def test_a_case_clears_when_the_rules_permit_it(engine: ComplianceEngine) -> None:
    result = engine.evaluate(_case(sale_date=date(2025, 1, 1)), rules=_rules(), as_of=TODAY)

    assert result.is_eligible is True
    assert result.blocking_reasons == ()
    assert result.earliest_contact_date == date(2025, 4, 1)


def test_a_case_inside_the_waiting_period_is_held(engine: ComplianceEngine) -> None:
    result = engine.evaluate(
        _case(sale_date=date(2026, 5, 1)), rules=_rules(waiting_period_days=90), as_of=TODAY
    )

    assert result.is_eligible is False
    assert BlockingReasonCode.WAITING_PERIOD in result.blocking_codes
    assert result.days_until_contact_allowed == 59


def test_the_reason_says_when_contact_becomes_lawful(engine: ComplianceEngine) -> None:
    result = engine.evaluate(
        _case(sale_date=date(2026, 5, 1)), rules=_rules(waiting_period_days=90), as_of=TODAY
    )

    assert "2026-07-30" in result.summary


# --------------------------------------------------------------------------------------
# Waiting periods
# --------------------------------------------------------------------------------------


def test_a_zero_day_wait_means_the_sale_date_itself() -> None:
    """Some states impose no wait; adding a day would hold back a lawful case."""
    assert compute_earliest_contact_date(date(2025, 1, 1), 0) == date(2025, 1, 1)


def test_the_earliest_date_itself_counts_as_elapsed() -> None:
    assert waiting_period_has_elapsed(date(2025, 1, 1), 90, date(2025, 4, 1)) is True
    assert waiting_period_has_elapsed(date(2025, 1, 1), 90, date(2025, 3, 31)) is False


def test_days_remaining_never_goes_negative() -> None:
    assert days_until_contact_allowed(date(2025, 1, 1), 90, date(2026, 1, 1)) == 0


def test_a_negative_waiting_period_is_rejected() -> None:
    with pytest.raises(ValueError):
        compute_earliest_contact_date(date(2025, 1, 1), -1)


# --------------------------------------------------------------------------------------
# Deadlines
# --------------------------------------------------------------------------------------


def test_a_passed_claim_deadline_blocks(engine: ComplianceEngine) -> None:
    result = engine.evaluate(
        _case(sale_date=date(2020, 1, 1)),
        rules=_rules(claim_deadline_days=365),
        as_of=TODAY,
    )

    assert result.is_eligible is False
    assert BlockingReasonCode.CLAIM_DEADLINE_PASSED in result.blocking_codes


def test_escheated_funds_block(engine: ComplianceEngine) -> None:
    result = engine.evaluate(
        _case(sale_date=date(2015, 1, 1)),
        rules=_rules(escheatment_period_days=1095),
        as_of=TODAY,
    )

    assert result.is_eligible is False
    assert BlockingReasonCode.ESCHEATED in result.blocking_codes


def test_a_licence_requirement_blocks_until_confirmed(engine: ComplianceEngine) -> None:
    result = engine.evaluate(
        _case(sale_date=date(2020, 1, 1)),
        rules=_rules(requires_locator_license=True),
        as_of=TODAY,
    )

    assert result.is_eligible is False
    assert BlockingReasonCode.LICENCE_REQUIRED in result.blocking_codes


def test_every_blocking_reason_is_reported_not_just_the_first(
    engine: ComplianceEngine,
) -> None:
    """A reviewer needs the whole picture, not one problem at a time."""
    result = engine.evaluate(
        _case(sale_date=date(2015, 1, 1)),
        rules=_rules(claim_deadline_days=365, escheatment_period_days=1095),
        as_of=TODAY,
    )

    assert len(result.blocking_reasons) >= 2


# --------------------------------------------------------------------------------------
# Fees
# --------------------------------------------------------------------------------------


def test_the_cap_itself_is_allowed() -> None:
    assert validate_fee_pct(20.0, 20.0) is True
    assert validate_fee_pct(20.01, 20.0) is False


def test_maximum_fee_is_computed_from_the_recovery() -> None:
    assert maximum_fee_for(_rules(), Decimal("10000.00")) == Decimal("2000.00")


def test_a_flat_cap_is_independent_of_the_recovery() -> None:
    flat = _rules(fee_cap_basis=FeeCapBasis.FLAT_AMOUNT, max_flat_fee_amount=500.0)

    assert maximum_fee_for(flat, Decimal("10000.00")) == Decimal("500")
    assert maximum_fee_for(flat, Decimal("999999.00")) == Decimal("500")


def test_an_unknown_cap_refuses_to_quote() -> None:
    """None must be read as "cannot quote", never as "no limit"."""
    unknown = _rules(max_contingency_fee_pct=None)

    assert maximum_fee_for(unknown, Decimal("10000")) is None
    permitted, reason = fee_is_permitted(unknown, Decimal("1.00"), Decimal("10000"))
    assert permitted is False
    assert "not been recorded" in reason


def test_a_fee_over_the_cap_is_refused() -> None:
    permitted, reason = fee_is_permitted(_rules(), Decimal("2500.00"), Decimal("10000.00"))

    assert permitted is False
    assert "exceeds" in reason


def test_a_state_with_no_cap_permits_any_fee() -> None:
    permitted, _ = fee_is_permitted(
        _rules(fee_cap_basis=FeeCapBasis.NONE), Decimal("9999"), Decimal("10000")
    )

    assert permitted is True


# --------------------------------------------------------------------------------------
# Disclosures and contract form
# --------------------------------------------------------------------------------------


def test_disclosures_are_reproduced_verbatim() -> None:
    """Statutes prescribe wording; paraphrasing it would put unauthorised text in front
    of a claimant."""
    wording = ("You may file this claim yourself at no cost.",)

    assert required_disclosures(_rules(required_disclosures=wording)) == wording


def test_contract_requirements_are_assembled_from_the_rules() -> None:
    rules = _rules(
        requires_written_contract=True,
        requires_notarized_contract=True,
        cooling_off_days=3,
        prohibits_assignment_of_claim=True,
    )

    requirements = contract_requirements(rules)

    assert any("written" in r for r in requirements)
    assert any("notaris" in r for r in requirements)
    assert any("3 day" in r for r in requirements)
    assert any("Assignment" in r for r in requirements)


def test_a_state_with_no_form_requirements_reports_none() -> None:
    assert contract_requirements(_rules()) == ()


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def test_shipped_states_are_present_but_unverified() -> None:
    """The corpus states have files with no invented values in them.

    This is the guard against someone filling in plausible-looking numbers without
    reading the statute: the moment a value appears, this test fails and whoever added it
    has to say where it came from.
    """
    loader = ComplianceRulesLoader()

    for code in ("MD", "IN"):
        rules = loader.load_or_none(code)
        assert rules is not None, f"{code}.yaml should exist"
        assert rules.verified is False
        assert rules.waiting_period_days is None
        assert rules.max_contingency_fee_pct is None


def test_no_state_is_currently_usable() -> None:
    """Nothing ships able to clear a case, because nothing has been researched yet."""
    assert ComplianceRulesLoader().verified_states() == ()


def test_a_missing_state_raises_when_asked_directly() -> None:
    with pytest.raises(StateRulesNotFoundError):
        ComplianceRulesLoader().load("QQ")


def test_a_missing_state_is_none_when_asked_softly() -> None:
    """A batch spanning many states must not die on the first unwritten file."""
    assert ComplianceRulesLoader().load_or_none("QQ") is None


def test_the_template_is_not_loaded_as_a_state() -> None:
    assert "_T" not in ComplianceRulesLoader().load_all()


def test_a_verified_file_with_gaps_is_rejected(tmp_path: Path) -> None:
    """Marking a file verified while leaving values blank must not be possible."""
    (tmp_path / "zz.yaml").write_text("state_code: ZZ\nverified: true\nwaiting_period_days: null\n")

    with pytest.raises(ComplianceConfigError, match="verified"):
        ComplianceRulesLoader(tmp_path).load("ZZ")


def test_a_mismatched_state_code_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "zz.yaml").write_text("state_code: YY\n")

    with pytest.raises(ComplianceConfigError, match="filename"):
        ComplianceRulesLoader(tmp_path).load("ZZ")


def test_a_malformed_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "zz.yaml").write_text("state_code: ZZ\nwaiting_period_days: soon\n")

    with pytest.raises(ComplianceConfigError):
        ComplianceRulesLoader(tmp_path).load("ZZ")


def test_compliance_errors_are_app_errors() -> None:
    assert issubclass(ComplianceError, AppError)
    assert issubclass(StateRulesNotFoundError, ComplianceError)


# --------------------------------------------------------------------------------------
# Batch behaviour
# --------------------------------------------------------------------------------------


def test_a_batch_spanning_unknown_states_still_completes(engine: ComplianceEngine) -> None:
    cases = [
        ComplianceCase(state_code="MD", sale_date=date(2020, 1, 1)),
        ComplianceCase(state_code="QQ", sale_date=date(2020, 1, 1)),
        ComplianceCase(state_code="IN", sale_date=date(2020, 1, 1)),
    ]

    results = engine.evaluate_many(cases, as_of=TODAY)

    assert len(results) == 3
    assert not any(r.is_eligible for r in results)


def test_the_real_corpus_states_are_all_blocked_today(engine: ComplianceEngine) -> None:
    """Until the statutes are recorded, no corpus county can be worked.

    Stated as a test so that bringing a state online is a visible, deliberate change
    rather than something that quietly starts happening.
    """
    for code in ("MD", "IN"):
        result = engine.evaluate(
            ComplianceCase(state_code=code, sale_date=date(2020, 1, 1)), as_of=TODAY
        )
        assert result.is_eligible is False
        assert BlockingReasonCode.RULES_UNVERIFIED in result.blocking_codes


# --------------------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------------------


def _run(*args: str) -> Result:
    """Invoke the CLI, returning stdout and stderr together as the operator sees them."""
    return CliRunner().invoke(build_app(), ["compliance", *args])


def _text(result: Result) -> str:
    return result.stdout + result.stderr


def test_cli_states_reports_that_nothing_is_usable() -> None:
    result = _run("states")

    assert result.exit_code == 0
    assert "0 usable" in _text(result)
    assert "No state can currently clear a case" in _text(result)


def test_cli_validate_names_what_is_missing_and_fails() -> None:
    """A non-zero exit matters: it is what stops a deployment script treating MD as ready."""
    result = _run("validate", "MD")

    assert result.exit_code == 1
    assert "waiting_period_days" in _text(result)
    assert "statute_citations" in _text(result)


def test_cli_validate_points_at_the_template_for_an_unknown_state() -> None:
    result = _run("validate", "QQ")

    assert result.exit_code == 1
    assert "_template.yaml" in _text(result)


def test_cli_check_refuses_an_unverified_state() -> None:
    result = _run("check", "MD", "--sale-date", "2020-01-01", "--amount", "10000")

    assert result.exit_code == 1
    assert "eligible:  False" in _text(result)
    assert BlockingReasonCode.RULES_UNVERIFIED.value in _text(result)


def test_cli_check_refuses_a_state_with_no_rules_at_all() -> None:
    result = _run("check", "QQ", "--sale-date", "2020-01-01")

    assert result.exit_code == 1
    assert BlockingReasonCode.NO_STATE_RULES.value in _text(result)


def test_cli_rejects_an_unparseable_date_rather_than_ignoring_it() -> None:
    """A silently dropped sale date would be read as "no date", which changes the verdict."""
    result = _run("check", "MD", "--sale-date", "01/03/2024")

    assert result.exit_code == 1
    assert "YYYY-MM-DD" in _text(result)
