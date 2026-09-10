from __future__ import annotations

import csv
import heapq
import io
import json
import logging
import os
import pickle
import tempfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.ports import DatasetFieldRecord, StoredRecord

SPOOL_MEMORY_LIMIT = 1024 * 1024
SORT_CHUNK_RECORDS = 1000
MAX_MERGE_RUNS = 64


def logged_export_stream(iterable):
    """Keep an accurate request event when transport fails; log no payload/exception."""
    try:
        try:
            yield from iterable
        finally:
            close = getattr(iterable, "close", None)
            if close is not None:
                close()
    except Exception:
        logging.getLogger(__name__).error("Dataset export stream failed")
        raise


class ExportGenerationError(Exception):
    """A known data or serialization failure that is safe to show to a user."""


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    content: BinaryIO
    media_type: str
    extension: str


def verify_artifact_readable(artifact: ExportArtifact) -> None:
    """Preflight the completed spool without retaining its contents in memory."""
    try:
        while artifact.content.read(64 * 1024):
            pass
        artifact.content.seek(0)
    except Exception:
        artifact.content.close()
        raise


def _plain_decimal(value: object) -> str:
    try:
        get_datatype("number").validate_typed(value)
    except DatatypeError:
        raise ExportGenerationError("The Dataset contains an invalid number") from None
    return format(value, "f")


def _excel_decimal(value: object) -> Decimal | str:
    rendered = _plain_decimal(value)
    significant_digits = len(rendered.lstrip("-").replace(".", "").lstrip("0"))
    # Excel numeric cells reliably retain at most 15 significant decimal digits.
    return value if significant_digits <= 15 else rendered


def _record_values(record: StoredRecord) -> dict[str, object]:
    values: dict[str, object] = {}
    for value in record.values:
        if value.field_key in values:
            raise ExportGenerationError("The Dataset contains duplicate field values")
        values[value.field_key] = value.value
    return values


def _validate_value(field: DatasetFieldRecord, value: object) -> object:
    try:
        datatype = get_datatype(field.data_type)
        datatype.validate_typed(value)
        if field.data_type != "string":
            datatype.text(value)
    except DatatypeError:
        raise ExportGenerationError("The Dataset contains an invalid stored value") from None
    return value


def _csv_string(value: str) -> str:
    # A leading apostrophe is visible on CSV re-import but makes spreadsheet formula parsing safe.
    meaningful = value.lstrip()
    if meaningful.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(target: BinaryIO, fields, records) -> None:
    target.write(b"\xef\xbb\xbf")
    text = io.TextIOWrapper(target, encoding="utf-8", newline="", write_through=True)
    writer = csv.writer(text, delimiter=",", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([field.key for field in fields])
    for record in records:
        values = _record_values(record)
        row = []
        for field in fields:
            value = values.get(field.key)
            if value is None:
                row.append("")
                continue
            value = _validate_value(field, value)
            if field.data_type == "string":
                row.append(_csv_string(value))
            elif field.data_type == "number":
                row.append(_plain_decimal(value))
            elif field.data_type == "boolean":
                row.append("true" if value else "false")
            else:
                row.append(get_datatype(field.data_type).text(value))
        writer.writerow(row)
    text.flush()
    text.detach()


def write_json(target: BinaryIO, fields, records) -> None:
    target.write(b"[")
    first_record = True
    for record in records:
        if not first_record:
            target.write(b",")
        first_record = False
        values = _record_values(record)
        target.write(b"{")
        for index, field in enumerate(fields):
            if index:
                target.write(b",")
            target.write(json.dumps(field.key, ensure_ascii=False).encode("utf-8"))
            target.write(b":")
            value = values.get(field.key)
            if value is None:
                rendered = "null"
            else:
                value = _validate_value(field, value)
                if field.data_type == "number":
                    rendered = _plain_decimal(value)
                elif field.data_type in {"integer", "float"}:
                    rendered = get_datatype(field.data_type).text(value)
                elif field.data_type in {"date", "time", "datetime", "timestamp"}:
                    rendered = json.dumps(get_datatype(field.data_type).text(value))
                else:
                    rendered = json.dumps(value, ensure_ascii=False, allow_nan=False)
            target.write(rendered.encode("utf-8"))
        target.write(b"}")
    target.write(b"]")


def write_xlsx(target: BinaryIO, fields, records, title: str) -> None:
    workbook = Workbook(write_only=True)
    try:
        worksheet = workbook.create_sheet(title=_worksheet_title(title))
        header = []
        for field in fields:
            cell = WriteOnlyCell(worksheet, value=field.key)
            cell.font = Font(bold=True)
            header.append(cell)
        worksheet.append(header)
        for record in records:
            values = _record_values(record)
            row = []
            for field in fields:
                value = values.get(field.key)
                if value is not None:
                    value = _validate_value(field, value)
                if (
                    field.data_type == "string"
                    and isinstance(value, str)
                    and value.lstrip().startswith(("=", "+", "-", "@"))
                ):
                    cell = WriteOnlyCell(worksheet, value=value)
                    cell.data_type = "s"
                    row.append(cell)
                elif field.data_type == "number" and value is not None:
                    row.append(_excel_decimal(value))
                elif field.data_type == "date" and value is not None:
                    cell = WriteOnlyCell(worksheet, value=value)
                    cell.number_format = "yyyy-mm-dd"
                    row.append(cell)
                elif value is not None and field.data_type in {
                    "integer",
                    "float",
                    "time",
                    "datetime",
                    "timestamp",
                }:
                    # Canonical text is lossless across Excel's numeric/time precision limits.
                    # Configurable genuine typed cells belong to REF-152.
                    row.append(get_datatype(field.data_type).text(value))
                else:
                    row.append(value)
            worksheet.append(row)
        worksheet.close()
        workbook.save(target)
    finally:
        try:
            for sheet in workbook.worksheets:
                if not sheet.closed:
                    sheet.close()
                # Workbook.close() only closes the archive, not write-only XML
                # generators or their temporary files when validation aborts save.
                writer = sheet._writer
                if writer is not None and os.path.exists(writer.out):
                    writer.cleanup()
        finally:
            workbook.close()


def _worksheet_title(value: str) -> str:
    cleaned = "".join("_" if character in "[]:*?/\\" else character for character in value)
    cleaned = cleaned.strip("'")[:31]
    return cleaned or "Dataset"


def externally_ordered_records(
    records: Iterable[StoredRecord], key: Callable[[StoredRecord], object]
) -> Iterator[StoredRecord]:
    """Bound ordering memory by sorting chunks and merging transient binary runs."""
    runs: list[BinaryIO] = []
    try:
        chunk: list[StoredRecord] = []
        for record in records:
            chunk.append(record)
            if len(chunk) == SORT_CHUNK_RECORDS:
                runs.append(_sorted_run(chunk, key))
                if len(runs) == MAX_MERGE_RUNS:
                    runs = [_consolidated_run(runs, key)]
                chunk = []
        if not runs:
            chunk.sort(key=key)
            yield from chunk
            return
        if chunk:
            runs.append(_sorted_run(chunk, key))
        yield from _merge_runs(runs, key)
    finally:
        for run in runs:
            run.close()


def _sorted_run(records: list[StoredRecord], key) -> BinaryIO:
    records.sort(key=key)
    run = tempfile.TemporaryFile(mode="w+b")
    for record in records:
        pickle.dump(record, run, protocol=pickle.HIGHEST_PROTOCOL)
    run.seek(0)
    return run


def _merge_runs(runs: list[BinaryIO], key) -> Iterator[StoredRecord]:
    heap = []
    for index, run in enumerate(runs):
        record = _next_pickled(run)
        if record is not None:
            heapq.heappush(heap, (key(record), index, record))
    while heap:
        _sort_key, index, record = heapq.heappop(heap)
        yield record
        replacement = _next_pickled(runs[index])
        if replacement is not None:
            heapq.heappush(heap, (key(replacement), index, replacement))


def _consolidated_run(runs: list[BinaryIO], key) -> BinaryIO:
    target = tempfile.TemporaryFile(mode="w+b")
    try:
        for record in _merge_runs(runs, key):
            pickle.dump(record, target, protocol=pickle.HIGHEST_PROTOCOL)
        target.seek(0)
        return target
    except Exception:
        target.close()
        raise
    finally:
        for run in runs:
            run.close()


def _next_pickled(run: BinaryIO) -> StoredRecord | None:
    try:
        return pickle.load(run)
    except EOFError:
        return None


def generate_export(format_name: str, fields, records, dataset_key: str) -> ExportArtifact:
    formats = {
        "csv": ("text/csv; charset=utf-8", write_csv),
        "json": ("application/json; charset=utf-8", write_json),
        "xlsx": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            write_xlsx,
        ),
    }
    if format_name not in formats:
        raise ExportGenerationError("Choose exactly one supported export format")
    target = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_LIMIT, mode="w+b")
    try:
        media_type, writer = formats[format_name]
        if format_name == "xlsx":
            writer(target, fields, records, dataset_key)
        else:
            writer(target, fields, records)
        target.seek(0)
        return ExportArtifact(target, media_type, format_name)
    except ExportGenerationError:
        target.close()
        raise
    except Exception:
        target.close()
        raise
    finally:
        close = getattr(records, "close", None)
        if close is not None:
            close()
