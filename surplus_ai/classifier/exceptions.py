from surplus_ai.utils.exceptions import AppError


class ClassificationError(AppError):
    """Base for every owner-classification failure."""


class KeywordConfigError(ClassificationError):
    """The entity keyword configuration is missing or invalid."""


class ModelNotLoadedError(ClassificationError):
    """A trained model was required but none has been loaded."""
