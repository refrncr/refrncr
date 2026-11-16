from types import SimpleNamespace

import pytest

from refrncr import datasets as dataset_domain
from refrncr import errors as shared_errors
from refrncr import services
from refrncr.authorization import (
    AccessManagementAuthorizationError,
    AccessManagementConflictError,
    AccessManagementNotFoundError,
    AccessManagementValidationError,
)
from refrncr.domain import advance_concept, require_concept, valid_identity
from refrncr.errors import (
    AccessManagementError,
    AuthorizationError,
    ConflictError,
    DatasetConflictError,
    DatasetError,
    DatasetNotFoundError,
    DatasetValidationError,
    DomainError,
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    UserManagementError,
    ValidationError,
)
from refrncr.hierarchies import (
    HierarchyConflictError,
    HierarchyError,
    HierarchyNotFoundError,
    HierarchyValidationError,
)
from refrncr.identity import (
    IdentityAuthorizationError,
    IdentityConflictError,
    IdentityError,
    IdentityNotFoundError,
    IdentityValidationError,
)
from refrncr.imports import (
    ImportConflictError,
    ImportNotFoundError,
    ImportValidationError,
)
from refrncr.imports.errors import ImportDomainError
from refrncr.versions import (
    VersionAuthorizationError,
    VersionConflictError,
    VersionError,
    VersionNotFoundError,
    VersionValidationError,
)


def test_compatibility_error_is_an_alias_and_services_reexports_categories():
    assert UserManagementError is DomainError
    assert services.UserManagementError is DomainError
    assert services.NotFoundError is NotFoundError
    assert services.ConflictError is ConflictError
    assert services.ValidationError is ValidationError
    assert services.AuthorizationError is AuthorizationError
    assert services.AccessManagementError is AccessManagementError


def test_authorization_is_a_category_and_access_management_is_a_domain():
    class DuplicateGrantError(AccessManagementError, ConflictError):
        pass

    assert issubclass(AuthorizationError, DomainError)
    assert not issubclass(AuthorizationError, AccessManagementError)
    assert issubclass(AccessManagementError, DomainError)
    assert not issubclass(AccessManagementError, AuthorizationError)
    assert not issubclass(AccessManagementError, NotFoundError | ConflictError | ValidationError)
    assert issubclass(DuplicateGrantError, AccessManagementError)
    assert issubclass(DuplicateGrantError, ConflictError)
    assert not issubclass(DuplicateGrantError, AuthorizationError)


def test_record_errors_have_a_domain_and_exact_category():
    assert issubclass(RecordValidationError, DatasetError)
    assert issubclass(RecordValidationError, ValidationError)
    assert not issubclass(RecordValidationError, ConflictError | NotFoundError)
    assert issubclass(RecordConflictError, DatasetError)
    assert issubclass(RecordConflictError, ConflictError)
    assert not issubclass(RecordConflictError, ValidationError | NotFoundError)


@pytest.mark.parametrize(
    ("error_type", "category", "other_categories"),
    [
        (DatasetNotFoundError, NotFoundError, ConflictError | ValidationError),
        (DatasetConflictError, ConflictError, NotFoundError | ValidationError),
        (DatasetValidationError, ValidationError, NotFoundError | ConflictError),
        (
            dataset_domain.DatasetAuthorizationError,
            AuthorizationError,
            NotFoundError | ConflictError | ValidationError,
        ),
    ],
)
def test_dataset_errors_have_a_domain_and_exact_category(error_type, category, other_categories):
    assert issubclass(error_type, DatasetError)
    assert issubclass(error_type, category)
    assert not issubclass(error_type, other_categories)


def test_dataset_error_exports_are_identical_and_have_one_category():
    names = (
        "DatasetError",
        "DatasetAuthorizationError",
        "DatasetNotFoundError",
        "DatasetConflictError",
        "DatasetValidationError",
        "RecordValidationError",
        "RecordConflictError",
        "StaleRecordError",
        "StaleFieldOrderError",
        "StaleDatasetOwnerError",
    )
    for name in names:
        assert getattr(dataset_domain, name) is getattr(shared_errors, name)

    categories = (NotFoundError, ConflictError, ValidationError, AuthorizationError)
    assert not any(issubclass(dataset_domain.DatasetError, category) for category in categories)
    for name in names[1:]:
        error_type = getattr(dataset_domain, name)
        assert sum(issubclass(error_type, category) for category in categories) == 1


def test_version_errors_have_a_domain_and_exact_category():
    categories = (NotFoundError, ConflictError, ValidationError, AuthorizationError)
    assert not any(issubclass(VersionError, category) for category in categories)
    for error_type, category in (
        (VersionNotFoundError, NotFoundError),
        (VersionConflictError, ConflictError),
        (VersionValidationError, ValidationError),
        (VersionAuthorizationError, AuthorizationError),
    ):
        assert issubclass(error_type, VersionError)
        assert issubclass(error_type, category)
        assert sum(issubclass(error_type, candidate) for candidate in categories) == 1


def test_import_errors_have_a_domain_and_exact_category():
    categories = (NotFoundError, ConflictError, ValidationError, AuthorizationError)
    assert not any(issubclass(ImportDomainError, category) for category in categories)
    for error_type, category in (
        (ImportNotFoundError, NotFoundError),
        (ImportConflictError, ConflictError),
        (ImportValidationError, ValidationError),
    ):
        assert issubclass(error_type, ImportDomainError)
        assert issubclass(error_type, category)
        assert sum(issubclass(error_type, candidate) for candidate in categories) == 1


def test_hierarchy_errors_have_a_domain_and_exact_category():
    categories = (NotFoundError, ConflictError, ValidationError, AuthorizationError)
    assert not any(issubclass(HierarchyError, category) for category in categories)
    for error_type, category in (
        (HierarchyNotFoundError, NotFoundError),
        (HierarchyConflictError, ConflictError),
        (HierarchyValidationError, ValidationError),
    ):
        assert issubclass(error_type, HierarchyError)
        assert issubclass(error_type, category)
        assert sum(issubclass(error_type, candidate) for candidate in categories) == 1


@pytest.mark.parametrize(
    ("error_type", "category", "other_categories"),
    [
        (
            IdentityNotFoundError,
            NotFoundError,
            ConflictError | ValidationError | AuthorizationError,
        ),
        (
            IdentityConflictError,
            ConflictError,
            NotFoundError | ValidationError | AuthorizationError,
        ),
        (
            IdentityValidationError,
            ValidationError,
            NotFoundError | ConflictError | AuthorizationError,
        ),
        (
            IdentityAuthorizationError,
            AuthorizationError,
            NotFoundError | ConflictError | ValidationError,
        ),
    ],
)
def test_identity_errors_have_a_domain_and_exact_category(error_type, category, other_categories):
    assert issubclass(error_type, IdentityError)
    assert issubclass(error_type, category)
    assert not issubclass(error_type, other_categories)


@pytest.mark.parametrize(
    ("error_type", "category", "other_categories"),
    [
        (
            AccessManagementNotFoundError,
            NotFoundError,
            ConflictError | ValidationError | AuthorizationError,
        ),
        (
            AccessManagementConflictError,
            ConflictError,
            NotFoundError | ValidationError | AuthorizationError,
        ),
        (
            AccessManagementValidationError,
            ValidationError,
            NotFoundError | ConflictError | AuthorizationError,
        ),
        (
            AccessManagementAuthorizationError,
            AuthorizationError,
            NotFoundError | ConflictError | ValidationError,
        ),
    ],
)
def test_access_management_errors_have_a_domain_and_exact_category(
    error_type, category, other_categories
):
    assert issubclass(error_type, AccessManagementError)
    assert issubclass(error_type, category)
    assert not issubclass(error_type, other_categories)


def test_access_management_conflicts_are_not_authorization_denials():
    error = AccessManagementConflictError("Grant already exists")

    assert isinstance(error, AccessManagementError | ConflictError)
    assert not isinstance(error, AuthorizationError)


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


def test_dataset_service_compatibility_exports_are_the_domain_classes():
    from refrncr.datasets import (
        DatasetBusinessKeyService,
        DatasetFieldService,
        DatasetRecordService,
    )
    from refrncr.datasets.business_key import DatasetBusinessKeyService as DomainBusinessKeys
    from refrncr.datasets.fields import DatasetFieldService as DomainFields
    from refrncr.datasets.records import DatasetRecordService as DomainRecords
    from refrncr.services import DatasetBusinessKeyService as CompatibilityBusinessKeys
    from refrncr.services import DatasetFieldService as CompatibilityFields
    from refrncr.services import DatasetRecordService as CompatibilityRecords

    assert DatasetFieldService is DomainFields is CompatibilityFields
    assert DatasetBusinessKeyService is DomainBusinessKeys is CompatibilityBusinessKeys
    assert DatasetRecordService is DomainRecords is CompatibilityRecords
