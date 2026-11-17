import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from refrncr.datasets.datatypes import DatatypeError, canonicalize, get_datatype
from refrncr.datasets.field_contracts import FIELD_KEY_PATTERN
from refrncr.presentation.metadata import (
    MAX_FIELD_DEFINITION,
    MAX_FIELD_UNIT,
    dataset_icon,
    optional_plain_text,
)

from .errors import DatasetValidationError


@dataclass(frozen=True, slots=True)
class DatasetDetails:
    id: str
    key: str
    name: str
    description: str
    owner_team_id: str
    owner_team_name: str
    record_count: int
    last_updated: datetime


def validate_dataset_key(key: str) -> str:
    if not isinstance(key, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", key) is None:
        raise DatasetValidationError(
            "Dataset key must contain 1-100 lowercase ASCII letters, numbers, "
            "underscores, or hyphens",
            field="key",
        )
    return key


def normalize_dataset_name(name: str) -> str:
    if not isinstance(name, str):
        raise DatasetValidationError("Dataset name must be text", field="name")
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 200:
        raise DatasetValidationError(
            "Dataset name must contain between 1 and 200 characters", field="name"
        )
    return normalized_name


def validate_dataset_description(description: str) -> str:
    if not isinstance(description, str):
        raise DatasetValidationError("Dataset description must be text", field="description")
    if len(description) > 10_000:
        raise DatasetValidationError(
            "Dataset description must not exceed 10000 characters", field="description"
        )
    return description


def validate_dataset_field_key(key: str) -> str:
    if not isinstance(key, str) or re.fullmatch(FIELD_KEY_PATTERN, key) is None:
        raise DatasetValidationError(
            "Field key must contain 1-100 lowercase ASCII letters, numbers, or underscores"
        )
    return key


def normalize_dataset_field_name(name: str) -> str:
    if not isinstance(name, str):
        raise DatasetValidationError("Field name must be text")
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 200:
        raise DatasetValidationError("Field name must contain between 1 and 200 characters")
    return normalized_name


def validate_field_metadata(definition, unit):
    try:
        return (
            optional_plain_text(definition, MAX_FIELD_DEFINITION, "Definition"),
            optional_plain_text(unit, MAX_FIELD_UNIT, "Unit"),
        )
    except ValueError as error:
        raise DatasetValidationError(str(error)) from None


def validate_dataset_icon(value):
    try:
        return dataset_icon(value)
    except ValueError as error:
        raise DatasetValidationError(str(error)) from None


def validate_dataset_field_type(data_type: str) -> str:
    try:
        return get_datatype(data_type).id
    except DatatypeError as error:
        raise DatasetValidationError(str(error)) from None


def require_identifier_type(data_type: str) -> None:
    validate_dataset_field_type(data_type)
    if not get_datatype(data_type).identifier_eligible:
        raise DatasetValidationError("Float cannot be part of a Record identifier")


def validate_dataset_field_flag(value: bool, label: str) -> bool:
    if not isinstance(value, bool):
        raise DatasetValidationError(f"{label} must be a boolean")
    return value


def canonicalize_record_value(data_type: str, raw: str | None) -> tuple[object, bytes] | None:
    try:
        return canonicalize(data_type, raw)
    except DatatypeError as error:
        raise DatasetValidationError(str(error)) from None
