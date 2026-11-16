from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from refrncr.errors import ConflictError, ImportDomainError, NotFoundError, ValidationError
from refrncr.imports.source_formats import (
    MAX_SOURCE_TIMEZONE_LENGTH,
    SourceFormatError,
    compose_format,
    formats_for,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DATA_ROWS = 10_000
MAX_COLUMNS = 250
MAX_ZIP_ENTRIES = 1_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_REPORTED_ROWS = 100
PREVIEW_ROWS = 50
IMPORT_TTL_SECONDS = 15 * 60
MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_WORKSHEET_SOURCE_ROWS = 100_001
PROFILE_SCAN_ROWS = 100
PROFILE_SAMPLE_VALUES = 25
DISPLAY_SAMPLE_VALUES = 3
DISPLAY_SAMPLE_CHARS = 80
CANCEL_WAIT_SECONDS = 5.0

UNREADABLE_XLSX_MESSAGE = (
    "We couldn't read this modern Excel workbook. Open it in Excel or LibreOffice, save a new "
    ".xlsx copy, and upload that copy. Password-protected, damaged, or unsupported workbooks "
    "cannot be imported."
)


class ImportValidationError(ImportDomainError, ValidationError):
    """A safe, user-facing import validation failure."""


class ImportConflictError(ImportDomainError, ConflictError):
    """Import work conflicts with its staged or persisted state."""


class ImportNotFoundError(ImportDomainError, NotFoundError):
    """An import or deliberately concealed Dataset does not exist."""


class SourceFormatComponentsError(ImportValidationError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("Choose a valid source type and its required format components.")


class ImportExpiredError(ImportDomainError, NotFoundError):
    """A previously valid staged import expired for its bound session and dataset."""


@dataclass(frozen=True, slots=True)
class SourceRow:
    number: int
    values: tuple[str, ...]
    formulas: frozenset[int] = frozenset()
    native: tuple[object | None, ...] = ()

    def source_value(self, index):
        return (
            self.native[index]
            if self.native and self.native[index] is not None
            else self.values[index]
        )


@dataclass(frozen=True, slots=True)
class ParsedSource:
    kind: str
    headers: tuple[str, ...]
    rows: tuple[SourceRow, ...]
    ignored_empty_rows: int
    delimiter: str | None = None
    worksheet: str | None = None
    worksheets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ColumnTypeProfile:
    values: tuple[object, ...]
    display_values: tuple[str, ...]
    compatible_types: frozenset[str]

    def compatibility(self, data_type: str) -> str:
        if not self.values:
            return "unknown"
        return "compatible" if data_type in self.compatible_types else "incompatible"


@dataclass(frozen=True, slots=True)
class MappingCandidate:
    field_key: str
    field_name: str
    confidence: str
    reason: str
    score: int


@dataclass(frozen=True, slots=True)
class ColumnMappingAdvice:
    header: str
    samples: tuple[str, ...]
    candidates: tuple[MappingCandidate, ...]
    selected: str | None
    warning: str | None = None

    @property
    def candidate_keys(self) -> tuple[str, ...]:
        return tuple(candidate.field_key for candidate in self.candidates)


@dataclass(frozen=True, slots=True)
class MappingCompleteness:
    unmapped_columns: int
    ignored_columns: int


@dataclass(frozen=True, slots=True)
class MappingIssue:
    code: str
    column_index: int | None = None
    field_key: str | None = None
    mode: str | None = None


def required_mapping_fields(fields, identifier, mode):
    return tuple(
        field
        for field in fields
        if (field.is_required and mode != "mapping_update") or field.key in identifier.field_keys
    )


def mapping_issues(mapping, fields, identifier, mode="create", expected_count=None):
    """Return the canonical coded mapping violations used by assistance and preview."""
    field_by_key = {field.key: field for field in fields}
    issues = []
    if expected_count is not None and len(mapping) != expected_count:
        issues.append(MappingIssue("mapping_count_mismatch", mode=mode))
    for index, selection in enumerate(mapping):
        if selection == "":
            issues.append(MappingIssue("column_unmapped", index, mode=mode))
        elif selection is not None and selection not in field_by_key:
            issues.append(MappingIssue("field_unknown", index, selection, mode))
        if selection in field_by_key and mapping.count(selection) > 1:
            issues.append(MappingIssue("field_mapped_twice", index, selection, mode))
    mapped = {selection for selection in mapping if selection in field_by_key}
    required = required_mapping_fields(fields, identifier, mode)
    issues.extend(
        MappingIssue("required_field_unmapped", field_key=field.key, mode=mode)
        for field in required
        if field.key not in mapped
    )
    return tuple(issues)


def validate_source_format_components(fields, mapping, components):
    """Normalize submitted source-format components and return coded column errors."""
    field_by_key = {field.key: field for field in fields}
    choices = {}
    errors = []
    for index, target in enumerate(mapping):
        if not target:
            continue
        raw = components[index] if index < len(components) else {}
        if (
            not isinstance(raw, Mapping)
            or set(raw) - {"source", "date", "time", "timezone", "fold"}
            or any(
                not isinstance(value, str) or len(value) > 100
                for name, value in raw.items()
                if name != "timezone"
            )
            or not isinstance(raw.get("timezone", ""), str)
            or len(raw.get("timezone", "")) > MAX_SOURCE_TIMEZONE_LENGTH
        ):
            errors.append(MappingIssue("source_format_incomplete", index, target or None))
            continue
        profile = raw.get("source", "")
        parts = {name: raw[name] for name in ("date", "time") if name in raw}
        composed = {"source": profile, **parts}
        field = field_by_key.get(target)
        datatype = field.data_type if field else None
        try:
            if profile in {"text", "native", "offset"}:
                profile = compose_format(datatype, composed)
            elif len(composed) != 1:
                raise SourceFormatError(
                    "Choose a valid source type and its required format components."
                )
            if profile and profile not in {item.id for item in formats_for(datatype)}:
                raise SourceFormatError("Choose an accepted source format for this datatype.")
            timezone = raw.get("timezone", "")
            fold = raw.get("fold", "")
            if timezone or fold:
                if datatype != "timestamp" or profile == "v1-offset" or not profile:
                    raise SourceFormatError(
                        "Timezone and repeated-time choices require a local Timestamp format."
                    )
            if fold not in {"", "earlier", "later"}:
                raise SourceFormatError("Choose a valid repeated-time decision.")
        except SourceFormatError:
            errors.append(MappingIssue("source_format_incomplete", index, target or None))
            continue
        if target:
            choices[target] = {"profile_id": profile, "timezone": timezone, "fold": fold}
    return choices, tuple(errors)


@dataclass(frozen=True, slots=True)
class MappingAssistance:
    columns: tuple[ColumnMappingAdvice, ...]
    completeness: MappingCompleteness
    fields: tuple = ()
    required_keys: tuple[str, ...] = ()
    identifier_keys: tuple[str, ...] = ()
    errors: tuple[MappingIssue, ...] = ()
    format_columns: tuple = ()
    remembered_formats: Mapping | None = None

    @property
    def mapping(self) -> tuple[str | None, ...]:
        return tuple(column.selected for column in self.columns)


@dataclass(frozen=True, slots=True)
class ImportIssue:
    row: int | None
    header: str | None
    field: str | None
    message: str
    code: str | None = None
    column_index: int | None = None
    field_key: str | None = None
    mode: str | None = None


def mapping_issue_message(issue: MappingIssue | ImportIssue) -> str:
    return {
        "mapping_count_mismatch": "Submit exactly one mapping for every source column.",
        "column_unmapped": "Choose a field or Do not import.",
        "field_unknown": "Choose a field from this dataset.",
        "field_mapped_twice": (
            "This field is mapped more than once. Choose a different field or Do not import."
        ),
        "required_field_unmapped": "Map every required and identifier field.",
        "source_format_incomplete": (
            "Choose a valid source type and its required format components."
        ),
        "source_format_ambiguous": "Choose an unambiguous source format for this field.",
    }.get(issue.code)


@dataclass(frozen=True, slots=True)
class ImportPreview:
    filename: str
    source: ParsedSource
    mapping: tuple[str | None, ...]
    valid_rows: int
    invalid_rows: int
    errors: tuple[ImportIssue, ...]
    total_errors: int
    row_report_truncated: bool
    sample: tuple[tuple[str, ...], ...]
    schema_fingerprint: tuple
    counts: dict | None = None
    differences: tuple = ()
    difference_digest: str = ""

    @property
    def valid(self) -> bool:
        return self.invalid_rows == 0 and self.total_errors == 0

    @property
    def non_row_errors(self) -> tuple[ImportIssue, ...]:
        return tuple(error for error in self.errors if error.row is None)

    @property
    def row_error_groups(self) -> tuple[tuple[int, tuple[ImportIssue, ...]], ...]:
        rows = sorted({error.row for error in self.errors if error.row is not None})
        return tuple(
            (row, tuple(error for error in self.errors if error.row == row)) for row in rows
        )


@dataclass(frozen=True, slots=True)
class StagedImport:
    token: str
    actor_id: str
    session_id: str
    dataset_id: str
    filename: str
    content: bytes
    kind: str
    worksheet: str | None
    mapping: tuple[str | None, ...] | None
    schema_fingerprint: tuple | None
    expires_at: float
    step: int = 1
    mapping_ready: bool = False
    validation_valid: bool = False
    busy: bool = False
    imported_count: int | None = None
    source_formats: Mapping[str, Mapping[str, str]] | None = None
    concept_id: str = ""
    mode: str = "create"
    difference_digest: str = ""
    confirmation: str = ""
