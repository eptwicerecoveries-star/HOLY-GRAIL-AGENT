from __future__ import annotations

from decimal import Decimal

from surplus_ai.compliance.state_rules import FeeCapBasis, StateComplianceRules


def validate_fee_pct(proposed_pct: float, cap_pct: float) -> bool:
    """Whether a contingency percentage is within a cap. The cap itself is allowed."""
    return proposed_pct <= cap_pct


def maximum_fee_for(rules: StateComplianceRules, recovery_amount: Decimal) -> Decimal | None:
    """The most that may lawfully be charged on a given recovery.

    Returns None when the state's fee position is unknown, which callers must treat as
    "cannot quote" rather than "no limit". The two are opposite conclusions and conflating
    them is how an unlawful fee gets agreed.
    """
    if rules.fee_cap_basis is FeeCapBasis.NONE:
        return None
    if rules.fee_cap_basis is FeeCapBasis.FLAT_AMOUNT:
        if rules.max_flat_fee_amount is None:
            return None
        return Decimal(str(rules.max_flat_fee_amount))
    if rules.max_contingency_fee_pct is None:
        return None
    return (
        recovery_amount * Decimal(str(rules.max_contingency_fee_pct)) / Decimal("100")
    ).quantize(Decimal("0.01"))


def fee_is_permitted(
    rules: StateComplianceRules, proposed_fee: Decimal, recovery_amount: Decimal
) -> tuple[bool, str]:
    """Whether a proposed fee is lawful, with the reason when it is not."""
    if rules.fee_cap_basis is FeeCapBasis.NONE:
        return True, "This state sets no statutory fee cap."
    if not rules.has_fee_limit:
        return False, (
            "This state's fee cap has not been recorded, so no fee can be agreed. "
            "Establish the statutory limit before quoting."
        )
    maximum = maximum_fee_for(rules, recovery_amount)
    if maximum is None:
        return False, "This state's fee cap could not be computed."
    if proposed_fee > maximum:
        return False, f"Proposed fee {proposed_fee} exceeds the statutory maximum {maximum}."
    return True, f"Within the statutory maximum of {maximum}."
