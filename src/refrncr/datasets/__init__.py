from importlib import import_module

from .errors import DatasetConflictError as DatasetConflictError
from .errors import DatasetError as DatasetError
from .errors import DatasetNotFoundError as DatasetNotFoundError
from .errors import DatasetValidationError as DatasetValidationError
from .errors import RecordConflictError as RecordConflictError
from .errors import RecordValidationError as RecordValidationError
from .errors import StaleDatasetOwnerError as StaleDatasetOwnerError
from .errors import StaleFieldOrderError as StaleFieldOrderError
from .errors import StaleRecordError as StaleRecordError

_LAZY_EXPORTS = {
    "DatasetDeletion": ("deletion", "DatasetDeletion"),
    "version_reads": ("deletion", "version_reads"),
    "DatasetService": ("service", "DatasetService"),
    "DatasetDetails": ("shared", "DatasetDetails"),
    "canonicalize_record_value": ("shared", "canonicalize_record_value"),
    "normalize_dataset_field_name": ("shared", "normalize_dataset_field_name"),
    "normalize_dataset_name": ("shared", "normalize_dataset_name"),
    "require_identifier_type": ("shared", "require_identifier_type"),
    "validate_dataset_description": ("shared", "validate_dataset_description"),
    "validate_dataset_field_flag": ("shared", "validate_dataset_field_flag"),
    "validate_dataset_field_key": ("shared", "validate_dataset_field_key"),
    "validate_dataset_field_type": ("shared", "validate_dataset_field_type"),
    "validate_dataset_icon": ("shared", "validate_dataset_icon"),
    "validate_dataset_key": ("shared", "validate_dataset_key"),
    "validate_field_metadata": ("shared", "validate_field_metadata"),
}


def __getattr__(name: str):
    """Load service exports only when callers request the compatibility surface."""
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted({*globals(), *_LAZY_EXPORTS})
