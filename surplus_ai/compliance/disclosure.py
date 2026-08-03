from __future__ import annotations

from surplus_ai.compliance.state_rules import StateComplianceRules


def required_disclosures(rules: StateComplianceRules) -> tuple[str, ...]:
    """Disclosures a state requires, exactly as its rules file lists them.

    Deliberately not a lookup against built-in wording. Required disclosure language is
    often prescribed word for word by statute, and paraphrasing it here would put text in
    front of a claimant that the statute does not authorise. The rules file holds whatever
    the statute says.
    """
    return rules.required_disclosures


def contract_requirements(rules: StateComplianceRules) -> tuple[str, ...]:
    """Form requirements a contract must meet in this state."""
    requirements: list[str] = []
    if rules.requires_written_contract:
        requirements.append("A written contract is required.")
    if rules.requires_notarized_contract:
        requirements.append("The contract must be notarised.")
    if rules.cooling_off_days:
        requirements.append(
            f"The claimant may cancel within {rules.cooling_off_days} day(s) of signing."
        )
    if rules.prohibits_assignment_of_claim:
        requirements.append("Assignment of the claim itself is prohibited in this state.")
    if rules.requires_locator_license:
        requirements.append("A locator or recovery-agent licence is required in this state.")
    return tuple(requirements)
