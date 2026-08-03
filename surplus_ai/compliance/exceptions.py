from surplus_ai.utils.exceptions import AppError


class ComplianceError(AppError):
    """Base for every compliance failure."""


class StateRulesNotFoundError(ComplianceError):
    """No rules file exists for a state.

    Raised only where a caller has asked for rules directly. The engine itself does not
    raise on a missing state: it records the absence as a blocking reason, so a case
    without verified rules is reported ineligible rather than crashing a batch.
    """


class ComplianceConfigError(ComplianceError):
    """A state rules file is present but malformed."""


class ComplianceValidationError(ComplianceError):
    """A proposed action conflicts with a state's rules."""
