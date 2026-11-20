from __future__ import annotations

import warnings

from refrncr import errors as _errors
from refrncr.authorization import (
    AccessManagementError as AccessManagementError,
)
from refrncr.authorization import (
    AuthorizationService as AuthorizationService,
)
from refrncr.authorization import (
    AuthorizedResourceSet as AuthorizedResourceSet,
)
from refrncr.authorization import (
    DatasetAuthorizationService as DatasetAuthorizationService,
)
from refrncr.authorization import (
    DatasetResourceResolver as DatasetResourceResolver,
)
from refrncr.authorization import GrantService as GrantService
from refrncr.authorization import ResourceTypeRegistry as ResourceTypeRegistry
from refrncr.authorization import (
    create_deprecated_grant_service as create_deprecated_grant_service,
)
from refrncr.datasets import MAX_RECORD_REVISION as MAX_RECORD_REVISION
from refrncr.datasets import DatasetBusinessKeyService as DatasetBusinessKeyService
from refrncr.datasets import DatasetConflictError as DatasetConflictError
from refrncr.datasets import DatasetDeletion as DatasetDeletion
from refrncr.datasets import DatasetDetails as DatasetDetails
from refrncr.datasets import DatasetError as DatasetError
from refrncr.datasets import DatasetFieldService as DatasetFieldService
from refrncr.datasets import DatasetNotFoundError as DatasetNotFoundError
from refrncr.datasets import DatasetRecordService as DatasetRecordService
from refrncr.datasets import DatasetService as DatasetService
from refrncr.datasets import DatasetValidationError as DatasetValidationError
from refrncr.datasets import RecordConflictError as RecordConflictError
from refrncr.datasets import RecordPage as RecordPage
from refrncr.datasets import RecordValidationError as RecordValidationError
from refrncr.datasets import StaleDatasetOwnerError as StaleDatasetOwnerError
from refrncr.datasets import StaleFieldOrderError as StaleFieldOrderError
from refrncr.datasets import StaleRecordError as StaleRecordError
from refrncr.datasets import canonicalize_record_value as canonicalize_record_value
from refrncr.datasets import normalize_dataset_field_name as normalize_dataset_field_name
from refrncr.datasets import normalize_dataset_name as normalize_dataset_name
from refrncr.datasets import parse_expected_revision as parse_expected_revision
from refrncr.datasets import validate_dataset_description as validate_dataset_description
from refrncr.datasets import validate_dataset_field_flag as validate_dataset_field_flag
from refrncr.datasets import validate_dataset_field_key as validate_dataset_field_key
from refrncr.datasets import validate_dataset_field_type as validate_dataset_field_type
from refrncr.datasets import validate_dataset_icon as validate_dataset_icon
from refrncr.datasets import validate_dataset_key as validate_dataset_key
from refrncr.datasets import validate_field_metadata as validate_field_metadata
from refrncr.datasets.datatypes import DATASET_FIELD_TYPES as DATASET_FIELD_TYPES
from refrncr.errors import (
    AuthorizationError as AuthorizationError,
)
from refrncr.errors import (
    ConflictError as ConflictError,
)
from refrncr.errors import NotFoundError as NotFoundError
from refrncr.errors import ValidationError as ValidationError
from refrncr.hierarchies import HierarchyError as HierarchyError
from refrncr.identity import (
    AuthenticatedPrincipal as AuthenticatedPrincipal,
)
from refrncr.identity import (
    AuthenticationService as AuthenticationService,
)
from refrncr.identity import (
    IdentityAuthorizationError as IdentityAuthorizationError,
)
from refrncr.identity import (
    IdentityConflictError as IdentityConflictError,
)
from refrncr.identity import (
    IdentityError as IdentityError,
)
from refrncr.identity import (
    IdentityNotFoundError as IdentityNotFoundError,
)
from refrncr.identity import (
    IdentityValidationError as IdentityValidationError,
)
from refrncr.identity import (
    InvalidLifecycleError as InvalidLifecycleError,
)
from refrncr.identity import (
    MembershipService as MembershipService,
)
from refrncr.identity import (
    SessionService as SessionService,
)
from refrncr.identity import (
    TeamService as TeamService,
)
from refrncr.identity import (
    UserService as UserService,
)
from refrncr.identity import (
    normalize_email as normalize_email,
)
from refrncr.identity import (
    normalize_team_name as normalize_team_name,
)
from refrncr.identity import (
    require_admin as require_admin,
)
from refrncr.identity import (
    validate_password as validate_password,
)
from refrncr.identity.shared import PASSWORD_HASHER as PASSWORD_HASHER
from refrncr.imports import ImportDomainError as ImportDomainError
from refrncr.persistence import PersistenceError as PersistenceError
from refrncr.versions import VersionError as VersionError

warnings.warn(
    "refrncr.services is deprecated; import the public integration contract from "
    "refrncr.integration instead",
    DeprecationWarning,
    stacklevel=2,
)

SUBJECT_TYPES = {"user", "team"}

# Compatibility facade while services move to domain packages. These assignments keep
# existing imports working without making ``errors.py`` depend on the facade.
DomainError = _errors.DomainError
UserManagementError = _errors.UserManagementError
