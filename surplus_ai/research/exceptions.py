from surplus_ai.utils.exceptions import AppError


class ResearchError(AppError):
    """Base for every research/enrichment failure."""


class ResearchConfigError(ResearchError):
    """Research configuration is missing or malformed."""


class ProviderNotFoundError(ResearchError):
    """A named provider is not registered."""


class ResearchPersistenceError(ResearchError):
    """A research result could not be written."""


class CandidateSelectionError(ResearchError):
    """A research target could not be selected."""
