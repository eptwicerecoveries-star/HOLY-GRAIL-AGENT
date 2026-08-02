from surplus_ai.utils.exceptions import AppError


class ParserError(AppError):
    """Base for every parsing failure."""


class UnreadablePDFError(ParserError):
    """The file could not be opened or is not a usable PDF."""


class NoTableFoundError(ParserError):
    """No candidate strategy produced a table that met the minimum quality bar."""


class HeaderDetectionError(ParserError):
    """No row in the candidate table was plausible as a header."""


class StrategyExecutionError(ParserError):
    """A single extraction strategy failed; the cascade continues with the others."""


class OCRRequiredError(ParserError):
    """The document's data region carries no text layer and OCR is not available."""
