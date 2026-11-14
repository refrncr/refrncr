from types import SimpleNamespace

import pytest

from refrncr import services
from refrncr.domain import advance_concept, require_concept, valid_identity
from refrncr.errors import (
    ConflictError,
    DatasetError,
    DomainError,
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    UserManagementError,
    ValidationError,
)


def test_compatibility_error_is_an_alias_and_services_reexports_categories():
    assert UserManagementError is DomainError
    assert services.UserManagementError is DomainError
    assert services.NotFoundError is NotFoundError
    assert services.ConflictError is ConflictError
    assert services.ValidationError is ValidationError


def test_record_errors_have_a_domain_and_exact_category():
    assert issubclass(RecordValidationError, DatasetError)
    assert issubclass(RecordValidationError, ValidationError)
    assert not issubclass(RecordValidationError, ConflictError | NotFoundError)
    assert issubclass(RecordConflictError, DatasetError)
    assert issubclass(RecordConflictError, ConflictError)
    assert not issubclass(RecordConflictError, ValidationError | NotFoundError)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00000000-0000-0000-0000-000000000001", True),
        ("00000000-0000-0000-0000-00000000000A", False),
        ("not-an-identity", False),
        (None, False),
    ],
)
def test_valid_identity(value, expected):
    assert valid_identity(value) is expected


def test_concept_helpers_preserve_existing_behavior():
    concept = object()
    concepts = SimpleNamespace(
        get=lambda dataset_id: concept, touch=lambda *args: touches.append(args)
    )
    uow = SimpleNamespace(concepts=concepts, now=lambda: "now")
    dataset = SimpleNamespace(id="dataset-id", revision=3)
    touches = []

    assert require_concept(uow, dataset.id) is concept
    advance_concept(uow, dataset)

    assert dataset.revision == 4
    assert touches == [(dataset.id, "now")]


def test_require_concept_preserves_conflict_contract():
    uow = SimpleNamespace(concepts=SimpleNamespace(get=lambda dataset_id: None))

    with pytest.raises(ConflictError, match="Start a New concept"):
        require_concept(uow, "dataset-id")
