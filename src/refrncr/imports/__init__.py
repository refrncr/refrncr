"""Dataset import domain services, contracts, parsing, and source formats."""

from importlib import import_module

from refrncr.imports.contracts import (
    ImportIssue,
    ImportPreview,
    ParsedSource,
    SourceRow,
    mapping_issue_message,
    validate_source_format_components,
)
from refrncr.imports.errors import (
    ImportConflictError,
    ImportDomainError,
    ImportExpiredError,
    ImportNotFoundError,
    ImportValidationError,
    SourceFormatComponentsError,
)
from refrncr.imports.parsing import (
    MAX_COLUMNS,
    MAX_COMPRESSION_RATIO,
    MAX_UPLOAD_BYTES,
    UNREADABLE_XLSX_MESSAGE,
    parse_source,
)
from refrncr.imports.profiling import (
    PROFILE_SCAN_ROWS,
    build_mapping_assistance,
    distinct_samples,
    normalize_mapping_name,
    profile_source_columns,
    rank_mapping_candidates,
)
from refrncr.imports.store import TransientImportStore

__all__ = [
    "MAX_COLUMNS",
    "MAX_COMPRESSION_RATIO",
    "MAX_UPLOAD_BYTES",
    "PROFILE_SCAN_ROWS",
    "DatasetRecordImportService",
    "ImportConflictError",
    "ImportDomainError",
    "ImportExpiredError",
    "ImportIssue",
    "ImportNotFoundError",
    "ImportPreview",
    "ImportValidationError",
    "ParsedSource",
    "SourceFormatComponentsError",
    "SourceRow",
    "TransientImportStore",
    "UNREADABLE_XLSX_MESSAGE",
    "build_mapping_assistance",
    "distinct_samples",
    "mapping_issue_message",
    "normalize_mapping_name",
    "parse_source",
    "profile_source_columns",
    "rank_mapping_candidates",
    "validate_source_format_components",
]


def __getattr__(name: str):
    if name == "DatasetRecordImportService":
        return getattr(import_module("refrncr.imports.service"), name)
    raise AttributeError(name)
