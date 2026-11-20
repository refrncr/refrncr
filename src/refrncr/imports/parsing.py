from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import date
from datetime import time as datetime_time
from pathlib import PurePath

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from refrncr.imports.contracts import ParsedSource, SourceRow
from refrncr.imports.errors import ImportValidationError
from refrncr.imports.profiling import normalize_mapping_name
from refrncr.imports.xml_security import require_xml_protection

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
            dialect = csv.excel()  # type: ignore[assignment]
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


def _xlsx_row(number, cells, headers):
    if len(cells) > MAX_COLUMNS:
        raise ImportValidationError("The worksheet has more than 250 columns")
    if len(cells) > len(headers):
        raise ImportValidationError(f"Row {number} has an inconsistent column count")
    cells = cells[: len(headers)] + (None,) * max(0, len(headers) - len(cells))
    values, formulas, native = [], set(), []
    for index, cell in enumerate(cells):
        native.append(
            cell.value
            if cell is not None and isinstance(cell.value, (date, datetime_time, int, float))
            else None
        )
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
    return SourceRow(number, tuple(values), frozenset(formulas), tuple(native))


def _xlsx_rows(iterator, headers):
    rows, empty = [], 0
    for number, cells in enumerate(iterator, 2):
        if number > MAX_WORKSHEET_SOURCE_ROWS:
            raise ImportValidationError("The worksheet contains too many physical rows")
        row = _xlsx_row(number, cells, headers)
        if all(value == "" for value in row.values) and not row.formulas:
            empty += 1
            continue
        rows.append(row)
        if len(rows) > MAX_DATA_ROWS:
            raise ImportValidationError("The worksheet has more than 10,000 data rows")
    if not rows:
        raise ImportValidationError("The worksheet must contain at least one data row")
    return rows, empty


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
        rows, empty = _xlsx_rows(iterator, headers)
        return ParsedSource(
            "xlsx", headers, tuple(rows), empty, worksheet=worksheet, worksheets=visible
        )
    finally:
        workbook.close()


def inspect_xlsx(content: bytes, worksheet: str | None = None) -> ParsedSource:
    require_xml_protection()
    try:
        return _inspect_xlsx(content, worksheet)
    except ImportValidationError:
        raise
    # SyntaxError covers ElementTree.ParseError and the optional lxml.XMLSyntaxError.
    # DefusedXmlException derives from ValueError, including during worksheet iteration.
    except (EOFError, InvalidFileException, KeyError, OSError, SyntaxError, ValueError) as exc:
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
