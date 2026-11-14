"""Small domain helpers shared by the transitional flat modules."""

from uuid import UUID

from refrncr.errors import ConflictError


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
