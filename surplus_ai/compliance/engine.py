from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

import structlog
from pydantic import BaseModel, ConfigDict, Field

from surplus_ai.compliance.disclosure import contract_requirements, required_disclosures
from surplus_ai.compliance.fee_cap import maximum_fee_for
from surplus_ai.compliance.rules_loader import ComplianceRulesLoader
from surplus_ai.compliance.state_rules import FeeCapBasis, StateComplianceRules
from surplus_ai.compliance.waiting_period import (
    compute_earliest_contact_date,
    days_until_contact_allowed,
)

logger = structlog.get_logger(__name__)


class BlockingReasonCode(str, Enum):
    """Why a case may not be worked. Coded so the cause can be counted and fixed."""

    NO_STATE_RULES = "no_state_rules"
    RULES_UNVERIFIED = "rules_unverified"
    RULES_INCOMPLETE = "rules_incomplete"
    NO_SALE_DATE = "no_sale_date"
    WAITING_PERIOD = "waiting_period"
    CLAIM_DEADLINE_PASSED = "claim_deadline_passed"
    ESCHEATED = "escheated"
    LICENCE_REQUIRED = "licence_required"


class ComplianceCase(BaseModel):
    """The facts about a case that bear on whether it may be worked."""

    model_config = ConfigDict(frozen=True)

    state_code: str
    sale_date: date | None = None
    surplus_amount: Decimal | None = None
    case_reference: str = ""


class BlockingReason(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: BlockingReasonCode
    detail: str


class ComplianceResult(BaseModel):
    """The verdict on one case, with the reasoning attached.

    `is_eligible` is never true by default. It is granted only when a verified rule set
    positively permits contact, so a state nobody has researched yet holds its cases back
    rather than releasing them.
    """

    model_config = ConfigDict(frozen=True)

    state_code: str
    is_eligible: bool = False
    earliest_contact_date: date | None = None
    days_until_contact_allowed: int | None = None
    fee_cap_pct: float | None = None
    fee_cap_basis: FeeCapBasis | None = None
    maximum_fee: Decimal | None = None
    disclosures_required: tuple[str, ...] = ()
    contract_requirements: tuple[str, ...] = ()
    blocking_reasons: tuple[BlockingReason, ...] = ()
    rules_verified: bool = False
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str = ""

    @property
    def blocking_codes(self) -> tuple[BlockingReasonCode, ...]:
        return tuple(r.code for r in self.blocking_reasons)

    @property
    def summary(self) -> str:
        if self.is_eligible:
            return "Eligible to contact."
        return " ".join(r.detail for r in self.blocking_reasons)


class ComplianceEngine:
    """Decides whether a case may lawfully be worked, and on what terms.

    The engine fails closed. Anywhere it lacks a fact it needs -- no rules file for the
    state, a file nobody has verified, a case with no sale date to count from -- it
    returns ineligible with the reason named, never eligible with a default filled in.

    That asymmetry is deliberate. Wrongly holding a case back costs one lead and is
    visible in the blocking counts; wrongly releasing one can mean contacting a former
    owner during a statutory blackout or agreeing a fee above a state's cap, which voids
    contracts and in some states is a criminal matter. The two errors are not comparable,
    so the code never guesses in the permissive direction.
    """

    def __init__(self, loader: ComplianceRulesLoader | None = None) -> None:
        self._loader = loader or ComplianceRulesLoader()

    def evaluate(
        self,
        case: ComplianceCase,
        rules: StateComplianceRules | None = None,
        as_of: date | None = None,
    ) -> ComplianceResult:
        """Evaluate one case against its state's rules."""
        today = as_of or datetime.now(UTC).date()
        resolved = rules if rules is not None else self._loader.load_or_none(case.state_code)

        if resolved is None:
            return self._blocked(
                case,
                BlockingReason(
                    code=BlockingReasonCode.NO_STATE_RULES,
                    detail=(
                        f"No compliance rules exist for {case.state_code.upper()}. Cases in "
                        "this state cannot be cleared until its statutes are recorded."
                    ),
                ),
            )

        gate = self._rules_gate(case, resolved)
        if gate is not None:
            return gate

        blocking: list[BlockingReason] = []
        earliest: date | None = None
        remaining: int | None = None

        if case.sale_date is None:
            blocking.append(
                BlockingReason(
                    code=BlockingReasonCode.NO_SALE_DATE,
                    detail=(
                        "This case has no sale date, so the statutory waiting period "
                        "cannot be counted from anything."
                    ),
                )
            )
        else:
            waiting = resolved.waiting_period_days or 0
            earliest = compute_earliest_contact_date(case.sale_date, waiting)
            remaining = days_until_contact_allowed(case.sale_date, waiting, today)
            if remaining > 0:
                blocking.append(
                    BlockingReason(
                        code=BlockingReasonCode.WAITING_PERIOD,
                        detail=(
                            f"{case.state_code.upper()} requires {waiting} day(s) after the "
                            f"sale before contact. That is {earliest.isoformat()}, "
                            f"{remaining} day(s) away."
                        ),
                    )
                )
            blocking.extend(self._deadline_reasons(case.sale_date, resolved, today))

        if resolved.requires_locator_license:
            blocking.append(
                BlockingReason(
                    code=BlockingReasonCode.LICENCE_REQUIRED,
                    detail=(
                        f"{case.state_code.upper()} requires a locator licence. Confirm the "
                        "licence is held before working cases in this state."
                    ),
                )
            )

        result = ComplianceResult(
            state_code=resolved.state_code,
            is_eligible=not blocking,
            earliest_contact_date=earliest,
            days_until_contact_allowed=remaining,
            fee_cap_pct=resolved.max_contingency_fee_pct,
            fee_cap_basis=resolved.fee_cap_basis,
            maximum_fee=(
                maximum_fee_for(resolved, case.surplus_amount)
                if case.surplus_amount is not None
                else None
            ),
            disclosures_required=required_disclosures(resolved),
            contract_requirements=contract_requirements(resolved),
            blocking_reasons=tuple(blocking),
            rules_verified=resolved.verified,
            notes=resolved.notes,
        )
        logger.info(
            "compliance_evaluated",
            state=resolved.state_code,
            eligible=result.is_eligible,
            blocking=[r.code.value for r in blocking],
        )
        return result

    def evaluate_many(
        self, cases: list[ComplianceCase], as_of: date | None = None
    ) -> list[ComplianceResult]:
        return [self.evaluate(case, as_of=as_of) for case in cases]

    def _rules_gate(
        self, case: ComplianceCase, rules: StateComplianceRules
    ) -> ComplianceResult | None:
        """Refuse to proceed on rules nobody has checked, or that are still incomplete."""
        if not rules.verified:
            return self._blocked(
                case,
                BlockingReason(
                    code=BlockingReasonCode.RULES_UNVERIFIED,
                    detail=(
                        f"The rules for {rules.state_code} have not been verified against "
                        "the statute. Fill in the values, confirm them, and set "
                        "verified: true before working cases in this state."
                    ),
                ),
                rules=rules,
            )
        missing = rules.missing_fields()
        if missing:
            return self._blocked(
                case,
                BlockingReason(
                    code=BlockingReasonCode.RULES_INCOMPLETE,
                    detail=(
                        f"The rules for {rules.state_code} are missing {list(missing)}, "
                        "which are needed before any case there can be cleared."
                    ),
                ),
                rules=rules,
            )
        return None

    def _deadline_reasons(
        self, sale_date: date, rules: StateComplianceRules, today: date
    ) -> list[BlockingReason]:
        """Whether the money is already out of reach."""
        reasons: list[BlockingReason] = []
        if rules.claim_deadline_days is not None:
            deadline = compute_earliest_contact_date(sale_date, rules.claim_deadline_days)
            if today > deadline:
                reasons.append(
                    BlockingReason(
                        code=BlockingReasonCode.CLAIM_DEADLINE_PASSED,
                        detail=(
                            f"The claim deadline of {deadline.isoformat()} has passed, so "
                            "this surplus can no longer be claimed."
                        ),
                    )
                )
        if rules.escheatment_period_days is not None:
            escheat = compute_earliest_contact_date(sale_date, rules.escheatment_period_days)
            if today > escheat:
                reasons.append(
                    BlockingReason(
                        code=BlockingReasonCode.ESCHEATED,
                        detail=(
                            f"The funds escheated on {escheat.isoformat()} and are no longer "
                            "held for the former owner."
                        ),
                    )
                )
        return reasons

    def _blocked(
        self,
        case: ComplianceCase,
        reason: BlockingReason,
        rules: StateComplianceRules | None = None,
    ) -> ComplianceResult:
        logger.info("compliance_blocked", state=case.state_code.upper(), reason=reason.code.value)
        return ComplianceResult(
            state_code=case.state_code.upper(),
            is_eligible=False,
            blocking_reasons=(reason,),
            rules_verified=bool(rules and rules.verified),
            notes=rules.notes if rules else "",
        )
