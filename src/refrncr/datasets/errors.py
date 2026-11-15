from refrncr.errors import (
    AuthorizationError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


class DatasetError(DomainError):
    """Dataset-domain failure."""


class DatasetNotFoundError(DatasetError, NotFoundError):
    """A Dataset object does not exist or is deliberately concealed."""


class DatasetConflictError(DatasetError, ConflictError):
    """Requested Dataset work conflicts with current domain state."""


class DatasetValidationError(DatasetError, ValidationError):
    """A Dataset request violates a domain constraint."""


class DatasetAuthorizationError(DatasetError, AuthorizationError):
    """An authenticated actor cannot perform the requested Dataset operation."""


class RecordValidationError(DatasetError, ValidationError):
    """Record values violate one or more Dataset field constraints."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Record contains invalid values")
        self.errors = errors


class RecordConflictError(DatasetError, ConflictError):
    """Record work conflicts with current Dataset state."""

    def __init__(self, message: str, field_key: str | None = None) -> None:
        super().__init__(message)
        self.field_key = field_key


class StaleRecordError(DatasetError, ConflictError):
    """The submitted Record revision no longer matches persisted state."""


class StaleFieldOrderError(DatasetError, ConflictError):
    """The submitted field order no longer matches persisted state."""


class StaleDatasetOwnerError(DatasetError, ConflictError):
    """Persisted ownership no longer matches the form's expected owner."""
