"""Small domain helpers shared by the transitional flat modules."""

from uuid import UUID

from refrncr.errors import ConflictError

# Persistence constraints and Dataset validation share these storage contracts.
# Keeping them neutral prevents persistence models from loading a domain package
# while that package is still being initialised.
DATASET_FIELD_TYPES = (
    "string",
    "integer",
    "number",
    "float",
    "boolean",
    "date",
    "time",
    "datetime",
    "timestamp",
)
MAX_RECORD_IDENTIFIER_BYTES = 2000
FIELD_KEY_PATTERN = r"[a-z0-9][a-z0-9_]{0,99}"


def valid_identity(value: str) -> bool:
    """Return whether *value* is the canonical text form of a UUID."""
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def require_concept(uow, dataset_id):
    """Return a Dataset's active concept or reject mutation without one."""
    concept = uow.concepts.get(dataset_id)
    if concept is None:
        raise ConflictError("Start a New concept before editing this Dataset.")
    return concept


def advance_concept(uow, dataset):
    """Advance a Dataset revision and touch its required active concept."""
    require_concept(uow, dataset.id)
    dataset.revision += 1
    uow.concepts.touch(dataset.id, uow.now())
