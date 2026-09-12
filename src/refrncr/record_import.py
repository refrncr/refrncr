from __future__ import annotations

import csv
import io
import os
import re
import secrets
import threading
import time
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from pathlib import PurePath
from types import MappingProxyType
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from refrncr.audit import record_audit
from refrncr.datatypes import DATASET_FIELD_TYPES, get_datatype
from refrncr.ports import PersistenceConflictError
from refrncr.services import (
    DatasetFieldService,
    DatasetRecordService,
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    ValidationError,
)
from refrncr.source_formats import (
    SourceFormatError,
    canonical_source,
    formats_for,
    propose_format,
    temporal_controls,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DATA_ROWS = 10_000
MAX_COLUMNS = 250
MAX_ZIP_ENTRIES = 1_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_REPORTED_ROWS = 100
PREVIEW_ROWS = 10
IMPORT_TTL_SECONDS = 15 * 60
MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_WORKSHEET_SOURCE_ROWS = 100_001
PROFILE_SCAN_ROWS = 100
PROFILE_SAMPLE_VALUES = 25
DISPLAY_SAMPLE_VALUES = 3
DISPLAY_SAMPLE_CHARS = 80

UNREADABLE_XLSX_MESSAGE = (
    "We couldn't read this modern Excel workbook. Open it in Excel or LibreOffice, save a new "
    ".xlsx copy, and upload that copy. Password-protected, damaged, or unsupported workbooks "
    "cannot be imported."
)


class ImportValidationError(ValidationError):
    """A safe, user-facing import validation failure."""


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
    required_mapped: int
    required_total: int
    missing_required: tuple[str, ...]
    missing_identifier: tuple[str, ...]
    unmapped_columns: int
    ignored_columns: int


@dataclass(frozen=True, slots=True)
class MappingAssistance:
    columns: tuple[ColumnMappingAdvice, ...]
    completeness: MappingCompleteness

    @property
    def mapping(self) -> tuple[str | None, ...]:
        return tuple(column.selected for column in self.columns)


@dataclass(frozen=True, slots=True)
class ImportError:
    row: int | None
    header: str | None
    field: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ImportPreview:
    filename: str
    source: ParsedSource
    mapping: tuple[str | None, ...]
    valid_rows: int
    invalid_rows: int
    errors: tuple[ImportError, ...]
    total_errors: int
    row_report_truncated: bool
    sample: tuple[tuple[str, ...], ...]
    schema_fingerprint: tuple

    @property
    def valid(self) -> bool:
        return self.invalid_rows == 0 and self.total_errors == 0

    @property
    def non_row_errors(self) -> tuple[ImportError, ...]:
        return tuple(error for error in self.errors if error.row is None)

    @property
    def row_error_groups(self) -> tuple[tuple[int, tuple[ImportError, ...]], ...]:
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


class TransientImportStore:
    """Bounded, process-local state for Community's single-process browser profile."""

    def __init__(
        self,
        *,
        clock=time.monotonic,
        ttl: int = IMPORT_TTL_SECONDS,
        capacity: int = 100,
        max_bytes: int = MAX_STAGED_BYTES,
    ):
        self.clock = clock
        self.ttl = ttl
        self.capacity = capacity
        self.max_bytes = max_bytes
        self._states: dict[str, StagedImport] = {}
        self._lock = threading.Lock()

    def create(
        self, actor_id, session_id, dataset_id, filename, content, kind, previous=None
    ) -> StagedImport:
        with self._lock:
            self._cleanup()
            if previous is not None:
                if self._states.get(previous.token) != previous or previous.busy:
                    raise NotFoundError("Import not found")
            if previous is None and any(
                (item.actor_id, item.session_id, item.dataset_id)
                == (actor_id, session_id, dataset_id)
                and item.imported_count is None
                for item in self._states.values()
            ):
                raise NotFoundError("Import not found")
            used_bytes = sum(len(item.content) for item in self._states.values())
            if previous is not None:
                used_bytes -= len(previous.content)
            if (
                len(self._states) - (previous is not None) >= self.capacity
                or used_bytes + len(content) > self.max_bytes
            ):
                raise ImportValidationError(
                    "Temporary import capacity is unavailable; try again later"
                )
            token = secrets.token_urlsafe(32)
            while token in self._states:
                token = secrets.token_urlsafe(32)
            state = StagedImport(
                token,
                actor_id,
                session_id,
                dataset_id,
                filename,
                content,
                kind,
                None,
                None,
                None,
                self.clock() + self.ttl,
            )
            if previous is not None:
                self._states.pop(previous.token)
            self._states[token] = state
            return state

    def get(self, token, actor_id, session_id, dataset_id) -> StagedImport:
        with self._lock:
            self._cleanup()
            state = self._states.get(token)
            if state is None or (state.actor_id, state.session_id, state.dataset_id) != (
                actor_id,
                session_id,
                dataset_id,
            ):
                raise NotFoundError("Import not found")
            return state

    def update(self, state: StagedImport, **changes) -> StagedImport:
        with self._lock:
            self._cleanup()
            if self._states.get(state.token) != state or state.busy:
                raise NotFoundError("Import not found")
            if changes.get("source_formats") is not None:
                changes["source_formats"] = MappingProxyType(
                    {
                        key: MappingProxyType(dict(choice))
                        for key, choice in changes["source_formats"].items()
                    }
                )
            updated = replace(state, **changes)
            self._states[state.token] = updated
            return updated

    def active(self, actor_id, session_id, dataset_id):
        with self._lock:
            self._cleanup()
            return next(
                (
                    state
                    for state in reversed(tuple(self._states.values()))
                    if (state.actor_id, state.session_id, state.dataset_id)
                    == (actor_id, session_id, dataset_id)
                    and state.imported_count is None
                ),
                None,
            )

    def claim(self, state):
        return self.update(state, busy=True)

    def finish(self, claimed, **changes):
        # A concurrent logout/expiry must never resurrect an import.
        with self._lock:
            self._cleanup()
            if self._states.get(claimed.token) == claimed:
                self._states[claimed.token] = replace(claimed, busy=False, **changes)

    def consume(self, token, actor_id, session_id, dataset_id) -> StagedImport:
        with self._lock:
            self._cleanup()
            state = self._states.get(token)
            if state is None or (state.actor_id, state.session_id, state.dataset_id) != (
                actor_id,
                session_id,
                dataset_id,
            ):
                raise NotFoundError("Import not found")
            if state.busy:
                raise NotFoundError("Import not found")
            self._states.pop(token)
            return state

    def cancel(self, token, actor_id, session_id, dataset_id) -> None:
        self.consume(token, actor_id, session_id, dataset_id)

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            for token in [
                key for key, value in self._states.items() if value.session_id == session_id
            ]:
                self._states.pop(token, None)

    def _cleanup(self):
        now = self.clock()
        for token in [key for key, value in self._states.items() if value.expires_at <= now]:
            self._states.pop(token, None)


def _safe_filename(filename: str) -> str:
    name = PurePath(filename.replace("\\", "/")).name.strip()
    return name[:255] or "import"


def normalize_mapping_name(value: str) -> str:
    """Return a deterministic comparison form while preserving no display state."""
    normalized = unicodedata.normalize("NFC", value.strip())
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = re.sub(r"[_\-\s]+", " ", normalized)
    decomposed = unicodedata.normalize("NFKD", normalized.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^\w]+", " ", without_marks).split())


def _safe_sample(value: str) -> str:
    if len(value) <= DISPLAY_SAMPLE_CHARS:
        return value
    return value[: DISPLAY_SAMPLE_CHARS - 1] + "…"


def distinct_samples(values, limit=DISPLAY_SAMPLE_VALUES):
    """Show a column's distinct values, so examples say something about its content.

    A column whose rows repeat ("candidate, candidate, candidate") tells the reader
    nothing. A column that really holds one value shows that one value once, which is
    itself the useful fact.
    """
    seen, distinct = set(), []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        distinct.append(value)
        if len(distinct) == limit:
            break
    return tuple(distinct)


def profile_source_columns(source: ParsedSource) -> tuple[ColumnTypeProfile, ...]:
    """Profile at most 100 rows and 25 non-empty, non-formula values per column."""
    sampled: list[list[object]] = [[] for _header in source.headers]
    for row in source.rows[:PROFILE_SCAN_ROWS]:
        for index, value in enumerate(row.values):
            value = row.source_value(index)
            if index in row.formulas or value is None or value == "":
                continue
            if len(sampled[index]) < PROFILE_SAMPLE_VALUES:
                sampled[index].append(value)
    profiles = []
    for values in sampled:
        compatible = set()
        for data_type in DATASET_FIELD_TYPES:
            if values and all(_profile_value_matches(data_type, value) for value in values):
                compatible.add(data_type)
        profiles.append(
            ColumnTypeProfile(
                tuple(values),
                tuple(_safe_sample(str(value)) for value in distinct_samples(values)),
                frozenset(compatible),
            )
        )
    return tuple(profiles)


def _profile_value_matches(data_type: str, value) -> bool:
    for profile in formats_for(data_type):
        try:
            canonical_source(data_type, value, profile.id)
            return True
        except SourceFormatError:
            continue
    # Local timestamps need a timezone decision, but still support a named mapping.
    if data_type == "timestamp":
        return _profile_value_matches("datetime", value)
    return False


def _name_match(header: str, field) -> tuple[int, str, str] | None:
    normalized_header = normalize_mapping_name(header)
    normalized_key = normalize_mapping_name(field.key)
    normalized_name = normalize_mapping_name(field.name)
    if header == field.key:
        return 600, "strong", "Exact field key match"
    if normalized_header == normalized_key:
        return 500, "strong", "Field key matches after separator normalization"
    if normalized_header == normalized_name:
        return 400, "strong", "Field name matches after separator normalization"
    header_tokens = frozenset(normalized_header.split())
    field_tokens = frozenset(normalized_name.split()) | frozenset(normalized_key.split())
    overlap = header_tokens & field_tokens
    if not overlap:
        return None
    difference = len(header_tokens ^ field_tokens)
    return 120 + 20 * len(overlap) - 10 * difference, "limited", "Header words overlap"


def rank_mapping_candidates(header: str, profile: ColumnTypeProfile, fields) -> tuple:
    candidates = []
    for field in fields:
        name_match = _name_match(header, field)
        if name_match is None:
            continue
        name_score, strength, reason = name_match
        compatibility = profile.compatibility(field.data_type)
        score = name_score
        if compatibility == "compatible":
            if field.data_type != "string":
                score += 20
                reason += f"; values match the {get_datatype(field.data_type).label} field type"
        elif compatibility == "incompatible":
            score -= 80
            reason += f"; values do not match the {get_datatype(field.data_type).label} field type"
        confidence = (
            "Low"
            if compatibility == "incompatible"
            else "High"
            if strength == "strong"
            else "Medium"
        )
        candidates.append(MappingCandidate(field.key, field.name, confidence, reason, score))
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.field_key))
    return tuple(candidates[:3])


def build_mapping_assistance(
    source: ParsedSource, fields, identifier, selected_mapping=None
) -> MappingAssistance:
    profiles = profile_source_columns(source)
    candidate_sets = [
        rank_mapping_candidates(header, profiles[index], fields)
        for index, header in enumerate(source.headers)
    ]
    warnings: list[str | None] = [None] * len(source.headers)
    for index, candidates in enumerate(candidate_sets):
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < 20:
            warnings[index] = "Multiple possible fields — choose one"
            candidate_sets[index] = tuple(
                replace(
                    candidate,
                    confidence="Medium" if candidate.confidence == "High" else candidate.confidence,
                    reason="Multiple possible fields — choose one",
                )
                for candidate in candidates
            )
    if selected_mapping is None:
        selections: list[str | None] = ["" if candidates else None for candidates in candidate_sets]
        eligible: dict[str, list[tuple[int, int]]] = {}
        for index, candidates in enumerate(candidate_sets):
            if not candidates or candidates[0].confidence != "High":
                continue
            if len(candidates) > 1 and candidates[0].score - candidates[1].score < 20:
                warnings[index] = "Multiple possible fields — choose one"
                continue
            eligible.setdefault(candidates[0].field_key, []).append((candidates[0].score, index))
        for field_key, matches in eligible.items():
            matches.sort(key=lambda item: (-item[0], item[1]))
            if len(matches) == 1 or matches[0][0] > matches[1][0]:
                selections[matches[0][1]] = field_key
                losers = matches[1:]
            else:
                losers = matches
            for _score, index in losers:
                warnings[index] = "Another source column also matches this field — choose one"
    else:
        selections = list(selected_mapping[: len(source.headers)])
        selections.extend("" for _index in range(len(source.headers) - len(selections)))
    columns = tuple(
        ColumnMappingAdvice(
            header,
            profiles[index].display_values,
            candidate_sets[index],
            selections[index],
            warnings[index],
        )
        for index, header in enumerate(source.headers)
    )
    mapped = {selection for selection in selections if selection}
    required = [field for field in fields if field.is_required]
    field_by_key = {field.key: field for field in fields}
    missing_required = tuple(field.name for field in required if field.key not in mapped)
    missing_identifier = tuple(
        field_by_key[key].name for key in identifier.field_keys if key not in mapped
    )
    completeness = MappingCompleteness(
        len(required) - len(missing_required),
        len(required),
        missing_required,
        missing_identifier,
        sum(selection == "" for selection in selections),
        sum(selection is None for selection in selections),
    )
    return MappingAssistance(columns, completeness)


def _validated_headers(values) -> tuple[str, ...]:
    headers = tuple("" if value is None else str(value).strip() for value in values)
    if not headers or any(not header for header in headers):
        raise ImportValidationError("Every source column must have a header")
    if len(headers) > MAX_COLUMNS:
        raise ImportValidationError("The file has more than 250 columns")
    normalized = [normalize_mapping_name(header) for header in headers]
    if any(not header for header in normalized):
        raise ImportValidationError("Every source column must have a meaningful header")
    if len(set(normalized)) != len(normalized):
        raise ImportValidationError("Source headers must be unique")
    return headers


def parse_csv(content: bytes) -> ParsedSource:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportValidationError("Upload a valid UTF-8 CSV file") from exc
    if "\x00" in text:
        raise ImportValidationError("Upload a valid UTF-8 CSV file")
    sample = text[:65536]
    try:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            first_line = sample.splitlines()[0] if sample.splitlines() else ""
            candidates = [delimiter for delimiter in ",;\t" if delimiter in first_line]
            if len(candidates) != 1:
                raise
            dialect = csv.excel()
            dialect.delimiter = candidates[0]
        reader = csv.reader(io.StringIO(text, newline=""), dialect, strict=True)
        raw_header = next(reader)
        headers = _validated_headers(raw_header)
        rows, empty = [], 0
        for number, values in enumerate(reader, 2):
            if len(values) != len(headers):
                raise ImportValidationError(f"Row {number} has an inconsistent column count")
            if all(value == "" for value in values):
                empty += 1
                continue
            rows.append(SourceRow(number, tuple(values)))
            if len(rows) > MAX_DATA_ROWS:
                raise ImportValidationError("The file has more than 10,000 data rows")
    except (csv.Error, StopIteration) as exc:
        raise ImportValidationError(
            "We couldn't read this CSV file. Save it as UTF-8 CSV with comma, semicolon, or tab "
            "separators and upload it again."
        ) from exc
    if not rows:
        raise ImportValidationError("The file must contain at least one data row")
    delimiter = {",": "comma", ";": "semicolon", "\t": "tab"}[dialect.delimiter]
    return ParsedSource("csv", headers, tuple(rows), empty, delimiter=delimiter)


def _xlsx_preflight(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise ImportValidationError("The workbook contains too many internal files")
            total = 0
            for entry in entries:
                parts = PurePath(entry.filename.replace("\\", "/")).parts
                if entry.filename.startswith(("/", "\\")) or ".." in parts:
                    raise ImportValidationError("The workbook contains unsafe internal paths")
                total += entry.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ImportValidationError("The workbook is too large when expanded")
                if entry.file_size and (
                    entry.compress_size == 0
                    or entry.file_size > entry.compress_size * MAX_COMPRESSION_RATIO
                ):
                    raise ImportValidationError("The workbook has an unsafe compression ratio")
                normalized = entry.filename.casefold()
                if (
                    "externallinks/" in normalized
                    or normalized.endswith("vbaproject.bin")
                    or normalized.endswith("connections.xml")
                ):
                    raise ImportValidationError(
                        "The workbook contains macros, external links, or external data "
                        "connections. Remove that active content, save a new .xlsx copy, and "
                        "upload it again."
                    )
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportValidationError(UNREADABLE_XLSX_MESSAGE) from exc


def _inspect_xlsx(content: bytes, worksheet: str | None = None) -> ParsedSource:
    _xlsx_preflight(content)
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    except (InvalidFileException, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise ImportValidationError(UNREADABLE_XLSX_MESSAGE) from exc
    try:
        visible = tuple(
            sheet.title for sheet in workbook.worksheets if sheet.sheet_state == "visible"
        )
        if not visible:
            raise ImportValidationError(
                "The workbook has no visible worksheets. Make exactly the worksheet you want to "
                "import visible, save the workbook, and upload it again."
            )
        if worksheet is None:
            return ParsedSource("xlsx", (), (), 0, worksheets=visible)
        if worksheet not in visible:
            raise ImportValidationError("Select exactly one visible worksheet")
        sheet = workbook[worksheet]
        if sheet.max_column is not None and sheet.max_column > MAX_COLUMNS:
            raise ImportValidationError("The worksheet has more than 250 columns")
        if sheet.max_row is not None and sheet.max_row > MAX_WORKSHEET_SOURCE_ROWS:
            raise ImportValidationError("The worksheet contains too many physical rows")
        iterator = sheet.iter_rows()
        try:
            header_cells = next(iterator)
        except StopIteration as exc:
            raise ImportValidationError("The worksheet is empty") from exc
        headers = _validated_headers(cell.value for cell in header_cells)
        rows, empty = [], 0
        for number, cells in enumerate(iterator, 2):
            if number > MAX_WORKSHEET_SOURCE_ROWS:
                raise ImportValidationError("The worksheet contains too many physical rows")
            if len(cells) > MAX_COLUMNS:
                raise ImportValidationError("The worksheet has more than 250 columns")
            if len(cells) > len(headers):
                raise ImportValidationError(f"Row {number} has an inconsistent column count")
            cells = cells[: len(headers)]
            if len(cells) < len(headers):
                cells += (None,) * (len(headers) - len(cells))
            values, formulas, native = [], set(), []
            for index, cell in enumerate(cells):
                native.append(
                    cell.value
                    if cell is not None
                    and isinstance(cell.value, (date, datetime_time, int, float))
                    else None
                )
                # Without worksheet dimensions a short row has no cell objects to pad with.
                if cell is None or (cell.value is None and cell.data_type != "f"):
                    values.append("")
                elif cell.data_type == "f":
                    formulas.add(index)
                    values.append("")
                elif isinstance(cell.value, bool):
                    values.append("true" if cell.value else "false")
                elif isinstance(cell.value, (date, datetime_time)):
                    values.append(cell.value.isoformat())
                else:
                    values.append(str(cell.value))
            if all(value == "" for value in values) and not formulas:
                empty += 1
                continue
            rows.append(SourceRow(number, tuple(values), frozenset(formulas), tuple(native)))
            if len(rows) > MAX_DATA_ROWS:
                raise ImportValidationError("The worksheet has more than 10,000 data rows")
        if not rows:
            raise ImportValidationError("The worksheet must contain at least one data row")
        return ParsedSource(
            "xlsx", headers, tuple(rows), empty, worksheet=worksheet, worksheets=visible
        )
    finally:
        workbook.close()


def inspect_xlsx(content: bytes, worksheet: str | None = None) -> ParsedSource:
    try:
        return _inspect_xlsx(content, worksheet)
    except ImportValidationError:
        raise
    except (EOFError, InvalidFileException, KeyError, OSError, ParseError, ValueError) as exc:
        raise ImportValidationError(UNREADABLE_XLSX_MESSAGE) from exc


def parse_source(filename: str, content: bytes, worksheet: str | None = None) -> ParsedSource:
    if not content:
        raise ImportValidationError("Choose a non-empty CSV or Excel file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportValidationError("The import file must not exceed 10 MiB")
    suffix = os.path.splitext(filename)[1].casefold()
    if suffix == ".csv" and not content.startswith(b"PK\x03\x04"):
        return parse_csv(content)
    if suffix == ".xlsx" and content.startswith(b"PK\x03\x04"):
        return inspect_xlsx(content, worksheet)
    raise ImportValidationError(
        "The file contents do not match a supported CSV or .xlsx file. Choose the original CSV, "
        "or open the workbook in Excel or LibreOffice and save a new .xlsx copy."
    )


class DatasetRecordImportService:
    def __init__(self, records: DatasetRecordService, store: TransientImportStore | None = None):
        self.records = records
        self.uow = records.uow
        self.authorization = records.authorization
        self.store = store or TransientImportStore()

    def active(self, actor_id, session_id, dataset_key):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        return self.store.active(actor_id, session_id, dataset.id)

    def state(self, actor_id, session_id, dataset_key, token):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self.store.get(token, actor_id, session_id, dataset.id)
        if state.busy:
            raise NotFoundError("Import not found")
        return state

    def begin(self, actor_id, session_id, dataset_key, filename, content):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        previous = self.store.active(actor_id, session_id, dataset.id)
        safe_name = _safe_filename(filename)
        source = parse_source(safe_name, content)
        if source.kind == "xlsx":
            worksheet = None
            if previous and previous.kind == "xlsx" and previous.worksheet in source.worksheets:
                worksheet = previous.worksheet
            elif len(source.worksheets) == 1:
                worksheet = source.worksheets[0]
            if worksheet:
                source = parse_source(safe_name, content, worksheet)
        assistance = self._assistance(dataset.id, source) if source.headers else None
        mapping = assistance.mapping if assistance else None
        if previous and previous.mapping is not None and source.headers:
            old = parse_source(previous.filename, previous.content, previous.worksheet)
            if old.kind == source.kind and old.worksheet == source.worksheet:
                retained = dict(zip(old.headers, previous.mapping, strict=False))
                with self.uow() as uow:
                    allowed = {
                        field.key for field in uow.dataset_fields.list_for_dataset(dataset.id)
                    }
                retained = {
                    header: target
                    for header, target in retained.items()
                    if header in source.headers and (target is None or target in allowed)
                }
                used = {target for target in retained.values() if target}
                mapping = tuple(
                    retained[header]
                    if header in retained
                    else ""
                    if mapping[index] in used
                    else mapping[index]
                    for index, header in enumerate(source.headers)
                )
                assistance = self._assistance(dataset.id, source, mapping)
        state = self.store.create(
            actor_id, session_id, dataset.id, safe_name, content, source.kind, previous
        )
        state = self.store.update(
            state, worksheet=source.worksheet, mapping=mapping, step=3 if source.headers else 2
        )
        return state, source, assistance

    def navigate(self, actor_id, session_id, dataset_key, token, step):
        state = self.state(actor_id, session_id, dataset_key, token)
        if state.imported_count is not None:
            if step != 5:
                raise NotFoundError("Import not found")
            return state
        source = parse_source(state.filename, state.content, state.worksheet)
        reachable = {1}
        if source.kind == "xlsx" and len(source.worksheets) > 1:
            reachable.add(2)
        if source.headers:
            reachable.add(3)
        if state.mapping_ready:
            reachable.add(4)
        if state.validation_valid:
            reachable.add(5)
        if step not in reachable:
            raise NotFoundError("Import not found")
        return self.store.update(state, step=step)

    def select_worksheet(self, actor_id, session_id, dataset_key, token, worksheet):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self.state(actor_id, session_id, dataset_key, token)
        if state.imported_count is not None:
            raise NotFoundError("Import not found")
        workbook = parse_source(state.filename, state.content)
        if workbook.kind != "xlsx" or len(workbook.worksheets) <= 1:
            raise NotFoundError("Import not found")
        source = parse_source(state.filename, state.content, worksheet)
        if state.worksheet != worksheet:
            state = self.store.update(
                state,
                worksheet=worksheet,
                mapping=None,
                source_formats=None,
                mapping_ready=False,
                validation_valid=False,
                schema_fingerprint=None,
                step=3,
            )
        else:
            state = self.store.update(state, step=3)
        return state, source, self._assistance(dataset.id, source, state.mapping)

    def mapping_assistance(self, actor_id, session_id, dataset_key, token):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self.store.get(token, actor_id, session_id, dataset.id)
        if state.imported_count is not None or state.busy:
            raise NotFoundError("Import not found")
        source = parse_source(state.filename, state.content, state.worksheet)
        if not source.headers:
            raise NotFoundError("Import not found")
        return state, source, self._assistance(dataset.id, source, state.mapping)

    def format_assistance(
        self, actor_id, session_id, dataset_key, token, mapping=None, choices=None
    ):
        state, source, assistance = self.mapping_assistance(
            actor_id, session_id, dataset_key, token
        )
        mapping = tuple(mapping) if mapping is not None else assistance.mapping
        with self.uow() as uow:
            fields = uow.dataset_fields.list_for_dataset(state.dataset_id)
            remembered = uow.dataset_fields.source_formats(state.dataset_id)
        selected = choices if choices is not None else (state.source_formats or {})
        return self._format_columns(source, fields, mapping, selected, remembered)

    @staticmethod
    def _format_columns(source, fields, mapping, choices, remembered):
        by_key = {field.key: field for field in fields}
        columns = []
        for index, key in enumerate(mapping):
            field = by_key.get(key)
            if field is None or index >= len(source.headers):
                columns.append(None)
                continue
            values = tuple(
                row.source_value(index) for row in source.rows if index not in row.formulas
            )
            raw_choice = choices.get(key, {})
            if (
                not isinstance(raw_choice, Mapping)
                or set(raw_choice) - {"profile_id", "timezone", "fold"}
                or any(
                    not isinstance(value, str) or len(value) > 100 for value in raw_choice.values()
                )
            ):
                raise ImportValidationError("Submit a valid bounded source-format choice.")
            choice = dict(raw_choice)
            if field.data_type != "timestamp" or choice.get("profile_id") == "v1-offset":
                if choice.get("timezone") or choice.get("fold"):
                    raise ImportValidationError(
                        "Timezone and repeated-time choices require a local Timestamp format."
                    )
                choice.pop("timezone", None)
                choice.pop("fold", None)
            error = None
            if not choice.get("profile_id"):
                choice, error = propose_format(field.data_type, values, remembered.get(key))
            examples = []
            seen_examples = set()
            failures = 0
            for row in source.rows:
                if index in row.formulas:
                    continue
                value = row.source_value(index)
                canonical = None
                if choice.get("profile_id"):
                    try:
                        canonical = canonical_source(field.data_type, value, **choice)
                    except SourceFormatError as exc:
                        failures += value is not None and value != ""
                        error = str(exc)
                if value == "" or value is None:
                    continue
                # Distinct values only: three identical examples say nothing.
                if len(examples) == DISPLAY_SAMPLE_VALUES:
                    continue
                example = {
                    "source": _safe_sample(str(value)),
                    "full_source": str(value)[:4096],
                    "stored": _safe_sample(canonical) if canonical is not None else "—",
                    "full_stored": canonical,
                }
                if str(value) in seen_examples:
                    continue
                seen_examples.add(str(value))
                examples.append(example)
            if failures:
                error = f"{failures} non-empty values do not fit. {error}"
            profiles = formats_for(field.data_type)
            selected_profile = next((p for p in profiles if p.id == choice.get("profile_id")), None)
            columns.append(
                {
                    "key": key,
                    "datatype": get_datatype(field.data_type).label,
                    "choice": choice,
                    "error": error,
                    "examples": examples,
                    "profiles": profiles,
                    "label": selected_profile.label if selected_profile else "Choose source format",
                    "applicable": field.data_type != "string",
                    # A CSV holds only text, so "a real date cell" cannot arise there.
                    "native_possible": source.kind == "xlsx",
                    "timestamp": field.data_type == "timestamp",
                    "temporal": temporal_controls(
                        field.data_type, selected_profile.id if selected_profile else ""
                    ),
                }
            )
        return columns

    def preview(self, actor_id, session_id, dataset_key, token, mapping, source_formats=None):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self.store.get(token, actor_id, session_id, dataset.id)
        if state.imported_count is not None or state.busy:
            raise NotFoundError("Import not found")
        source = parse_source(state.filename, state.content, state.worksheet)
        if not source.headers:
            raise NotFoundError("Import not found")
        mapping = tuple(mapping)
        if mapping != state.mapping:
            state = self.store.update(
                state,
                mapping=mapping,
                source_formats={
                    key: choice
                    for key, choice in (state.source_formats or {}).items()
                    if key in mapping
                    and state.mapping
                    and key in state.mapping
                    and mapping.index(key) == state.mapping.index(key)
                },
                mapping_ready=False,
                validation_valid=False,
                schema_fingerprint=None,
                step=3,
            )
        columns = self.format_assistance(
            actor_id, session_id, dataset_key, token, mapping, source_formats
        )
        choices = {column["key"]: column["choice"] for column in columns if column is not None}
        if choices != state.source_formats:
            state = self.store.update(
                state,
                source_formats=choices,
                validation_valid=False,
                mapping_ready=False,
                schema_fingerprint=None,
                step=3,
            )
        preview = self._validate(dataset.id, state.filename, source, mapping, choices)
        self.store.update(
            state,
            mapping=preview.mapping,
            schema_fingerprint=preview.schema_fingerprint,
            mapping_ready=not preview.non_row_errors,
            validation_valid=preview.valid,
            step=3 if preview.non_row_errors else 4,
        )
        return preview

    def saved_preview(self, actor_id, session_id, dataset_key, token):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self.store.get(token, actor_id, session_id, dataset.id)
        if not state.mapping_ready or state.imported_count is not None or state.busy:
            raise NotFoundError("Import not found")
        source = parse_source(state.filename, state.content, state.worksheet)
        preview = self._validate(
            dataset.id, state.filename, source, state.mapping, state.source_formats
        )
        self.store.update(
            state,
            validation_valid=preview.valid,
            mapping_ready=not preview.non_row_errors,
            schema_fingerprint=preview.schema_fingerprint,
        )
        return preview

    def confirm(
        self, actor_id, session_id, dataset_key, token, *, require_confirmation=False
    ) -> int:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow() as read_uow:
            target = read_uow.datasets.find_by_key(validated_key)
        if target is None:
            raise NotFoundError("Dataset not found")
        self.records._require_access(actor_id, target, "editor")
        state = self.store.get(token, actor_id, session_id, target.id)
        if (
            state.busy
            or state.imported_count is not None
            or (require_confirmation and (state.step != 5 or not state.validation_valid))
        ):
            raise NotFoundError("Import not found")
        if not state.mapping_ready:
            raise NotFoundError("Import not found")
        claimed = self.store.claim(state)
        try:
            with self.uow(write=True, serialized=True) as uow:
                dataset = uow.datasets.find_by_key(validated_key, for_update=True)
                self.records._require_access(actor_id, dataset, "editor", uow=uow)
                if dataset.id != state.dataset_id:
                    raise NotFoundError("Import not found")
                source = parse_source(state.filename, state.content, state.worksheet)
                preview, validated = self._validate_in_uow(
                    uow, dataset.id, state.filename, source, state.mapping, state.source_formats
                )
                if preview.schema_fingerprint != state.schema_fingerprint:
                    if preview.schema_fingerprint[1:] == state.schema_fingerprint[1:]:
                        raise ImportValidationError(
                            "The import is no longer valid; the Dataset changed. Validate again."
                        )
                    raise ImportValidationError(
                        "The Dataset schema changed; validate the import again"
                    )
                if not preview.valid:
                    raise ImportValidationError("The import is no longer valid; validate it again")
                try:
                    for values, identifier in validated:
                        record = uow.dataset_records.create(
                            dataset_id=dataset.id, identifier_key=identifier
                        )
                        uow.flush()
                        uow.dataset_records.add_values(record, values)
                        uow.flush()
                except PersistenceConflictError as error:
                    DatasetRecordService._raise_record_conflict(error)
                if validated:
                    uow.dataset_fields.remember_source_formats(
                        dataset.id, state.source_formats or {}
                    )
                    from refrncr.services import _advance_concept

                    _advance_concept(uow, dataset)
                    dataset.content_updated_at = datetime.now(UTC)
                    record_audit(
                        uow,
                        actor_user_id=actor_id,
                        object_type="dataset",
                        object_id=dataset.id,
                        action="dataset.records_imported",
                        details={
                            "format": source.kind,
                            "record_count": len(validated),
                            "rejected_row_count": preview.invalid_rows,
                        },
                    )

        except (ValidationError, RecordConflictError):
            self.store.finish(claimed, validation_valid=False, step=4)
            raise
        except Exception:
            self.store.finish(claimed)
            raise
        self.store.finish(
            claimed,
            content=b"",
            mapping=None,
            source_formats=None,
            schema_fingerprint=None,
            imported_count=len(validated),
            step=5,
        )
        return len(validated)

    def cancel(self, actor_id, session_id, dataset_key, token):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        self.store.cancel(token, actor_id, session_id, dataset.id)

    def revoke_session(self, session_id: str) -> None:
        self.store.revoke_session(session_id)

    def _assistance(self, dataset_id, source, selected_mapping=None):
        with self.uow() as uow:
            fields = uow.dataset_fields.list_for_dataset(dataset_id)
            identifier = uow.dataset_business_keys.get(dataset_id)
        return build_mapping_assistance(source, fields, identifier, selected_mapping)

    def _validate(self, dataset_id, filename, source, mapping, source_formats=None):
        with self.uow() as uow:
            preview, _ = self._validate_in_uow(
                uow, dataset_id, filename, source, mapping, source_formats
            )
            return preview

    def _validate_in_uow(self, uow, dataset_id, filename, source, mapping, source_formats=None):
        fields = uow.dataset_fields.list_for_dataset(dataset_id)
        identifier = uow.dataset_business_keys.get(dataset_id)
        field_by_key = {field.key: field for field in fields}
        mapping = tuple(mapping)
        mapping_errors = []
        if len(mapping) != len(source.headers):
            mapping_errors.append(
                ImportError(None, None, None, "Submit exactly one mapping for every source column")
            )
        unmapped = [value for value in mapping if value == ""]
        unknown = [
            value for value in mapping if value not in {None, ""} and value not in field_by_key
        ]
        duplicate = [
            value for value in set(mapping) if value not in {None, ""} and mapping.count(value) > 1
        ]
        if unknown:
            mapping_errors.append(
                ImportError(None, None, None, "A mapping refers to an unknown Dataset field")
            )
        if unmapped:
            mapping_errors.append(
                ImportError(
                    None,
                    None,
                    None,
                    "Map or explicitly mark every source column as Ignored",
                )
            )
        if duplicate:
            mapping_errors.append(
                ImportError(None, None, None, "A Dataset field may be mapped only once")
            )
        required = {field.key for field in fields if field.is_required} | set(identifier.field_keys)
        missing = sorted(required - {value for value in mapping if value})
        if missing:
            mapping_errors.append(
                ImportError(None, None, None, "Map every required and identifier field")
            )
        fingerprint = (
            uow.datasets.get(dataset_id).revision,
            tuple(
                (
                    field.id,
                    field.key,
                    field.name,
                    field.data_type,
                    field.is_required,
                    field.is_unique,
                )
                for field in fields
            ),
            identifier.field_keys,
        )
        source_formats = source_formats or {}
        for index, key in enumerate(mapping):
            if key in field_by_key and not source_formats.get(key, {}).get("profile_id"):
                mapping_errors.append(
                    ImportError(
                        None,
                        source.headers[index] if index < len(source.headers) else None,
                        field_by_key[key].name,
                        "Choose an unambiguous source format for this field.",
                    )
                )
        if mapping_errors:
            return ImportPreview(
                filename,
                source,
                mapping,
                0,
                0,
                tuple(mapping_errors),
                len(mapping_errors),
                False,
                (),
                fingerprint,
            ), []
        choice_errors = {}
        seen_ids, seen_unique, validated, errors, invalid_rows = set(), set(), [], [], set()
        for row in source.rows:
            submitted = {}
            row_errors = []
            for index, target in enumerate(mapping):
                if target is None or index in row.formulas:
                    continue
                try:
                    submitted[target] = canonical_source(
                        field_by_key[target].data_type,
                        row.source_value(index),
                        **source_formats[target],
                    )
                except SourceFormatError as exc:
                    if exc.requires_choice:
                        choice_errors[target] = ImportError(
                            None, source.headers[index], field_by_key[target].name, str(exc)
                        )
                    row_errors.append(
                        ImportError(
                            row.number, source.headers[index], field_by_key[target].name, str(exc)
                        )
                    )
            for index in row.formulas:
                if mapping[index] is not None:
                    row_errors.append(
                        ImportError(
                            row.number,
                            source.headers[index],
                            field_by_key[mapping[index]].name,
                            "Formula cells cannot be imported. Replace the formula with its value "
                            "or mark this source column as Ignored.",
                        )
                    )
            try:
                values, encoded = DatasetRecordService._validated_values(
                    fields, identifier, submitted
                )
            except RecordValidationError as error:
                values, encoded = (), b""
                for key, message in error.errors.items():
                    if any(item.field == field_by_key[key].name for item in row_errors):
                        continue
                    index = mapping.index(key)
                    row_errors.append(
                        ImportError(
                            row.number, source.headers[index], field_by_key[key].name, message
                        )
                    )
            except ValidationError as error:
                values, encoded = (), b""
                row_errors.append(ImportError(row.number, None, None, str(error)))
            if not row_errors:
                if encoded in seen_ids:
                    row_errors.append(
                        ImportError(
                            row.number, None, None, "Record identifier is duplicated in this file"
                        )
                    )
                for value in values:
                    marker = (value.field_id, value.canonical_key)
                    if value.is_unique and marker in seen_unique:
                        row_errors.append(
                            ImportError(
                                row.number,
                                source.headers[mapping.index(value.field_key)],
                                field_by_key[value.field_key].name,
                                "Unique value is duplicated in this file",
                            )
                        )
                if not row_errors:
                    seen_ids.add(encoded)
                    seen_unique.update(
                        (value.field_id, value.canonical_key) for value in values if value.is_unique
                    )
                    validated.append((row, values, encoded))
            if row_errors:
                invalid_rows.add(row.number)
                errors.extend(row_errors)
        existing_identifiers = uow.dataset_records.existing_identifier_keys(
            dataset_id, tuple(encoded for _row, _values, encoded in validated)
        )
        unique_candidates = tuple(
            (value.field_id, value.canonical_key)
            for _row, values, _encoded in validated
            for value in values
            if value.is_unique
        )
        existing_unique = uow.dataset_records.existing_unique_value_keys(
            dataset_id, unique_candidates
        )
        accepted = []
        for row, values, encoded in validated:
            database_errors = []
            if encoded in existing_identifiers:
                database_errors.append(
                    ImportError(
                        row.number,
                        None,
                        None,
                        "Record identifier already exists in this Dataset",
                    )
                )
            for value in values:
                marker = (value.field_id, value.canonical_key)
                if value.is_unique and marker in existing_unique:
                    database_errors.append(
                        ImportError(
                            row.number,
                            source.headers[mapping.index(value.field_key)],
                            field_by_key[value.field_key].name,
                            "Unique value already exists in this Dataset",
                        )
                    )
            if database_errors:
                invalid_rows.add(row.number)
                errors.extend(database_errors)
            else:
                accepted.append((values, encoded))
        errors.extend(choice_errors.values())
        errors.sort(
            key=lambda item: (
                item.row is not None,
                item.row or 0,
                item.header or "",
                item.field or "",
                item.message,
            )
        )
        reported_rows = set(sorted(invalid_rows)[:MAX_REPORTED_ROWS])
        reported_errors = tuple(
            error for error in errors if error.row is None or error.row in reported_rows
        )
        sample = tuple(
            tuple(
                next(
                    (
                        get_datatype(value.data_type).text(value.value)
                        for value in values
                        if value.field_key == field.key
                    ),
                    "",
                )
                for field in fields
                if field.key in mapping
            )
            for values, _encoded in accepted[:PREVIEW_ROWS]
        )
        preview = ImportPreview(
            filename,
            source,
            mapping,
            len(source.rows) - len(invalid_rows),
            len(invalid_rows),
            reported_errors,
            len(errors),
            len(invalid_rows) > MAX_REPORTED_ROWS,
            sample,
            fingerprint,
        )
        return preview, accepted
