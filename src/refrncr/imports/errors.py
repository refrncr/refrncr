"""Persistence-neutral errors for the import domain."""

from refrncr.errors import ConflictError, DomainError, NotFoundError, ValidationError


class ImportDomainError(DomainError):
    """Import-domain failure; named distinctly from Python's built-in ImportError."""


class ImportValidationError(ImportDomainError, ValidationError):
    """A safe, user-facing import validation failure."""


class ImportConflictError(ImportDomainError, ConflictError):
    """Import work conflicts with its staged or persisted state."""


class ImportNotFoundError(ImportDomainError, NotFoundError):
    """An import or deliberately concealed Dataset does not exist."""


class ImportExpiredError(ImportDomainError, NotFoundError):
    """A previously valid staged import expired for its bound session and dataset."""


class SourceFormatComponentsError(ImportValidationError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("Choose a valid source type and its required format components.")


class SourceFormatError(ImportDomainError, ValidationError):
    """Bounded source-format failure that never contains imported content."""

    def __init__(self, message, *, requires_choice=False):
        super().__init__(message)
        self.requires_choice = requires_choice
