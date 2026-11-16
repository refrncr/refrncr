"""Dataset-domain services, errors, and supporting behavior."""

# The error exports intentionally precede services: authorization imports the
# Dataset categories while the package is being initialized.
# ruff: noqa: I001

from .errors import (
    DatasetAuthorizationError,
    DatasetConflictError,
    DatasetError,
    DatasetNotFoundError,
    DatasetValidationError,
    RecordConflictError,
    RecordValidationError,
    StaleDatasetOwnerError,
    StaleFieldOrderError,
    StaleRecordError,
)
from .business_key import DatasetBusinessKeyService
from .deletion import DatasetDeletion, version_reads
from .fields import DatasetFieldService
from refrncr.ports import RecordPage

from .records import MAX_RECORD_REVISION, DatasetRecordService, parse_expected_revision
from .service import DatasetService
from .shared import (
    DatasetDetails,
    canonicalize_record_value,
    normalize_dataset_field_name,
    normalize_dataset_name,
    require_identifier_type,
    validate_dataset_description,
    validate_dataset_field_flag,
    validate_dataset_field_key,
    validate_dataset_field_type,
    validate_dataset_icon,
    validate_dataset_key,
    validate_field_metadata,
)

__all__ = (
    "MAX_RECORD_REVISION",
    "DatasetAuthorizationError",
    "DatasetBusinessKeyService",
    "DatasetConflictError",
    "DatasetDeletion",
    "DatasetDetails",
    "DatasetError",
    "DatasetFieldService",
    "DatasetNotFoundError",
    "DatasetRecordService",
    "DatasetService",
    "DatasetValidationError",
    "RecordConflictError",
    "RecordPage",
    "RecordValidationError",
    "StaleDatasetOwnerError",
    "StaleFieldOrderError",
    "StaleRecordError",
    "canonicalize_record_value",
    "normalize_dataset_field_name",
    "normalize_dataset_name",
    "parse_expected_revision",
    "require_identifier_type",
    "validate_dataset_description",
    "validate_dataset_field_flag",
    "validate_dataset_field_key",
    "validate_dataset_field_type",
    "validate_dataset_icon",
    "validate_dataset_key",
    "validate_field_metadata",
    "version_reads",
)
