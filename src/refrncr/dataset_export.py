from __future__ import annotations

import csv
import heapq
import io
import json
import logging
import os
import pickle
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo

from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.hierarchy import applicable
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
    download_name: str | None = None


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


def _hierarchy_value(field, node):
    return next((value for value in node.values if value.field_id == field.id), None)


def _hierarchy_text(field, value):
    if value is None:
        return None
    value = _validate_value(field, value.value)
    if field.data_type == "number":
        return _plain_decimal(value)
    return get_datatype(field.data_type).text(value)


def _hierarchy_json_value(field, value):
    value = _validate_value(field, value.value)
    if field.data_type == "number":
        return _plain_decimal(value)
    if field.data_type == "boolean":
        return "true" if value else "false"
    if field.data_type in {"integer", "float"}:
        return get_datatype(field.data_type).text(value)
    return json.dumps(
        value if field.data_type == "string" else get_datatype(field.data_type).text(value),
        ensure_ascii=False,
    )


def _hierarchy_export_value(field, node, parent_ids):
    if not applicable(field, node, parent_ids):
        return None
    value = _hierarchy_value(field, node)
    if value is None:
        return None
    value = _validate_value(field, value.value)
    if field.data_type in {"number", "integer", "float", "boolean"}:
        return value
    return get_datatype(field.data_type).text(value)


def _hierarchy_columns(fields, reserved):
    columns = []
    for field in fields:
        column = field.key
        if column in reserved:
            column = f"Field: {column}"
        while column in columns:
            column = f"Field: {column}"
        columns.append(column)
    return columns


def _hierarchy_order(nodes):
    by_parent = {}
    for node in nodes:
        by_parent.setdefault(node.parent_id, []).append(node)
    for siblings in by_parent.values():
        siblings.sort(key=lambda node: (node.name.casefold(), node.name, node.id))
    result = []
    pending = list(reversed(by_parent.get(None, ())))
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(reversed(by_parent.get(node.id, ())))
    result.extend(node for node in nodes if node not in result)
    return result


def _hierarchy_paths(nodes):
    by_id = {node.id: node for node in nodes}
    paths = {}
    for node in nodes:
        path, current, visited = [], node, set()
        while current is not None and current.id not in visited:
            visited.add(current.id)
            path.append(current)
            current = by_id.get(current.parent_id)
        paths[node.id] = tuple(reversed(path))
    return paths


def _hierarchy_cell(field, node, parent_ids):
    if not applicable(field, node, parent_ids):
        return "Not applicable"
    rendered = _hierarchy_text(field, _hierarchy_value(field, node))
    return "Empty" if rendered is None else rendered


def _hierarchy_data_rows(fields, nodes, *, include_ids=True):
    parent_ids = {node.parent_id for node in nodes}
    by_id = {node.id: node for node in nodes}
    columns = _hierarchy_columns(fields, {"Node ID", "Parent ID", "Name", "Parent"})
    headers = (
        ["Node ID", "Parent ID", "Name", "Parent", *columns]
        if include_ids
        else ["Name", "Parent", *columns]
    )
    rows = []
    for node in _hierarchy_order(nodes):
        row = [
            node.name,
            by_id[node.parent_id].name if node.parent_id in by_id else "",
            *[_hierarchy_export_value(field, node, parent_ids) for field in fields],
        ]
        rows.append([node.id, node.parent_id or "", *row] if include_ids else row)
    return headers, rows


def _hierarchy_flattened_rows(fields, nodes, *, include_ids=True):
    parent_ids = {node.parent_id for node in nodes}
    paths = _hierarchy_paths(nodes)
    leaves = [
        node
        for node in _hierarchy_order(nodes)
        if node.parent_id is not None and node.id not in parent_ids
    ]
    max_ancestors = max((len(paths[node.id]) - 1 for node in leaves), default=0)
    level_headers = [f"Level {index}" for index in range(1, max_ancestors + 1)]
    columns = []
    field_columns = []
    reserved = {"Node ID", "Parent ID", "Leaf", *level_headers}
    for field in fields:
        if field.hierarchy_scope == "all_nodes":
            for level in level_headers:
                field_columns.append((field, level))
                columns.append(
                    _hierarchy_columns((field,), reserved | set(columns))[0] + f" {level}"
                )
        field_columns.append((field, "Leaf"))
        columns.append(_hierarchy_columns((field,), reserved | set(columns))[0] + " Leaf")
    prefix = ["Node ID", "Parent ID"] if include_ids else []
    headers = [*prefix, *level_headers, "Leaf", *columns]
    rows = []
    for leaf in leaves:
        path = paths[leaf.id]
        ancestors = path[:-1]
        row = [leaf.id, leaf.parent_id or ""] if include_ids else []
        row.extend([node.name for node in ancestors])
        row.extend([""] * (max_ancestors - len(ancestors)))
        row.append(leaf.name)
        for field, target in field_columns:
            node = (
                leaf
                if target == "Leaf"
                else ancestors[int(target.split()[-1]) - 1]
                if int(target.split()[-1]) <= len(ancestors)
                else None
            )
            row.append(None if node is None else _hierarchy_export_value(field, node, parent_ids))
        rows.append(row)
    return headers, rows


def _reporting_field_columns(fields, reserved):
    """Keep reporting headings machine-friendly when a Field uses a reserved key."""
    field_keys = {field.key for field in fields}
    columns = []
    for field in fields:
        column = field.key
        other_field_keys = field_keys - {field.key}
        while column in reserved or column in other_field_keys or column in columns:
            column = f"field_{column}"
        columns.append(column)
    return columns


def _hierarchy_reporting_rows(fields, nodes):
    ordered = _hierarchy_order(nodes)
    parent_ids = {node.parent_id for node in nodes}
    paths = _hierarchy_paths(nodes)
    max_depth = max((len(path) for path in paths.values()), default=0)
    level_names = [f"level_{index}" for index in range(1, max_depth + 1)]
    all_node_fields = [field for field in fields if field.hierarchy_scope == "all_nodes"]
    per_level_names = [
        f"level_{index}_{field.key}"
        for index in range(1, max_depth + 1)
        for field in all_node_fields
    ]
    reserved = {
        "node_id",
        "name",
        "parent_id",
        "depth",
        "is_leaf",
        "sort_order",
        "path",
        *level_names,
        *per_level_names,
    }
    field_columns = _reporting_field_columns(fields, reserved)
    headers = [
        "node_id",
        "name",
        "parent_id",
        *field_columns,
        "depth",
        "is_leaf",
        "sort_order",
        "path",
        *level_names,
        *per_level_names,
    ]
    rows = []
    for sort_order, node in enumerate(ordered, start=1):
        path = paths[node.id]
        filled_path = [*path, *([node] * (max_depth - len(path)))]
        row = [
            node.id,
            node.name,
            node.parent_id or "",
            *[_hierarchy_export_value(field, node, parent_ids) for field in fields],
            len(path),
            node.id not in parent_ids,
            sort_order,
            "/".join(item.name for item in path),
            *[item.name for item in filled_path],
        ]
        row.extend(
            _hierarchy_export_value(field, item, parent_ids)
            for item in filled_path
            for field in all_node_fields
        )
        rows.append(row)
    return headers, rows


def _write_hierarchy_csv(target, fields, nodes, variant):
    if variant == "data":
        headers, rows = _hierarchy_data_rows(fields, nodes)
    elif variant == "flattened":
        headers, rows = _hierarchy_flattened_rows(fields, nodes)
    else:
        headers, rows = _hierarchy_reporting_rows(fields, nodes)
    target.write(b"\xef\xbb\xbf")
    text = io.TextIOWrapper(target, encoding="utf-8", newline="", write_through=True)
    writer = csv.writer(text, delimiter=",", lineterminator="\r\n")
    writer.writerow([_csv_string(str(header)) for header in headers])
    for row in rows:
        writer.writerow(
            [
                _csv_string(value)
                if isinstance(value, str)
                else "true"
                if variant == "reporting" and value is True
                else "false"
                if variant == "reporting" and value is False
                else value
                for value in row
            ]
        )
    text.flush()
    text.detach()


def write_hierarchy_csv_bundle(target, fields, nodes, dataset_key):
    with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for variant in ("data", "flattened"):
            csv_target = io.BytesIO()
            _write_hierarchy_csv(csv_target, fields, nodes, variant)
            archive.writestr(f"{dataset_key}-{variant}.csv", csv_target.getvalue())


def _write_hierarchy_sheet(worksheet, headers, rows):
    header = []
    for value in headers:
        cell = WriteOnlyCell(worksheet, value=value)
        cell.font = Font(bold=True)
        header.append(cell)
    worksheet.append(header)
    for row in rows:
        safe_row = []
        for value in row:
            if isinstance(value, str):
                cell = WriteOnlyCell(worksheet, value=value)
                # User-controlled node names and values must never become formulas.
                cell.data_type = "s"
                safe_row.append(cell)
            else:
                safe_row.append(value)
        worksheet.append(safe_row)


def write_hierarchy_xlsx(target, fields, nodes, title: str) -> None:
    workbook = Workbook(write_only=True)
    try:
        data = workbook.create_sheet(title="Data")
        data_headers, data_rows = _hierarchy_data_rows(fields, nodes)
        _write_hierarchy_sheet(data, data_headers, data_rows)
        flattened = workbook.create_sheet(title="Flattened")
        flat_headers, flat_rows = _hierarchy_flattened_rows(fields, nodes)
        _write_hierarchy_sheet(flattened, flat_headers, flat_rows)
        hierarchy = workbook.create_sheet(title="Hierarchy")
        hierarchy_headers = ["Name", "Path"]
        paths = _hierarchy_paths(nodes)
        hierarchy_rows = [
            [
                node.name,
                " / ".join(item.name for item in paths[node.id]),
            ]
            for node in _hierarchy_order(nodes)
        ]
        _write_hierarchy_sheet(hierarchy, hierarchy_headers, hierarchy_rows)
        workbook.save(target)
    finally:
        for sheet in workbook.worksheets:
            if not sheet.closed:
                sheet.close()
            writer = sheet._writer
            if writer is not None and os.path.exists(writer.out):
                writer.cleanup()
        workbook.close()


def write_hierarchy_reporting_xlsx(target, fields, nodes) -> None:
    headers, rows = _hierarchy_reporting_rows(fields, nodes)
    workbook = Workbook(write_only=True)
    try:
        worksheet = workbook.create_sheet(title="Reporting")
        worksheet.freeze_panes = "A2"
        _write_hierarchy_sheet(worksheet, headers, rows)
        if headers:
            table = Table(
                displayName="ReportingTable",
                ref=f"A1:{get_column_letter(len(headers))}{len(rows) + 1}",
            )
            table.tableColumns = [
                TableColumn(id=index, name=header) for index, header in enumerate(headers, start=1)
            ]
            table.tableStyleInfo = TableStyleInfo(
                name="TableStyleMedium2",
                showFirstColumn=False,
                showLastColumn=False,
                showRowStripes=True,
                showColumnStripes=False,
            )
            # The write-only warning in add_table concerns missing manual columns;
            # they are defined above, so register the completed table directly.
            worksheet.tables.add(table)
        workbook.save(target)
    finally:
        for sheet in workbook.worksheets:
            if not sheet.closed:
                sheet.close()
            writer = sheet._writer
            if writer is not None and os.path.exists(writer.out):
                writer.cleanup()
        workbook.close()


def write_hierarchy_json(target, fields, nodes, *, dataset_key, provenance):
    metadata = {
        "dataset_key": dataset_key,
        "provenance": provenance,
        "fields": [
            {
                "key": field.key,
                "name": field.name,
                "data_type": field.data_type,
                "scope": field.hierarchy_scope,
            }
            for field in fields
        ],
    }
    target.write(b'{"schema_version":1,"metadata":')
    target.write(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    target.write(b',"nodes":[')
    parent_ids = {node.parent_id for node in nodes}
    first_node = True
    for node in _hierarchy_order(nodes):
        if not first_node:
            target.write(b",")
        first_node = False
        target.write(
            json.dumps(
                {"node_id": node.id, "parent_id": node.parent_id, "name": node.name},
                ensure_ascii=False,
                separators=(",", ":"),
            )[:-1].encode("utf-8")
        )
        target.write(b',"values":{')
        first_value = True
        for field in fields:
            value = _hierarchy_value(field, node)
            if not first_value:
                target.write(b",")
            first_value = False
            target.write(json.dumps(field.key, ensure_ascii=False).encode("utf-8"))
            target.write(b":")
            if not applicable(field, node, parent_ids) or value is None:
                target.write(b"null")
            else:
                rendered = _hierarchy_json_value(field, value)
                target.write(rendered.encode("utf-8"))
        target.write(b"}}")
    target.write(b"]}")


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


def generate_export(
    format_name: str,
    fields,
    records,
    dataset_key: str,
    *,
    hierarchy_nodes: Sequence | None = None,
    hierarchy_variant: str = "data",
    provenance: dict | None = None,
) -> ExportArtifact:
    formats = {
        "csv": ("text/csv; charset=utf-8", write_csv),
        "csv_zip": ("application/zip", write_hierarchy_csv_bundle),
        "json": ("application/json; charset=utf-8", write_json),
        "xlsx": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            write_xlsx,
        ),
    }
    if format_name not in formats:
        raise ExportGenerationError("Choose exactly one supported export format")
    is_hierarchy = hierarchy_nodes is not None
    if is_hierarchy and hierarchy_variant not in {"data", "flattened", "reporting"}:
        raise ExportGenerationError("Choose Data, Flattened or Reporting for a hierarchy export")
    if is_hierarchy and hierarchy_variant == "reporting" and format_name not in {"csv", "xlsx"}:
        raise ExportGenerationError("Reporting tables are available as CSV or Excel")
    if format_name == "csv_zip" and not is_hierarchy:
        raise ExportGenerationError("CSV bundles are only available for hierarchy exports")
    target = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_LIMIT, mode="w+b")
    try:
        media_type, writer = formats[format_name]
        if is_hierarchy and format_name == "csv_zip":
            writer(target, fields, hierarchy_nodes, dataset_key)
        elif is_hierarchy and format_name == "csv":
            _write_hierarchy_csv(target, fields, hierarchy_nodes, hierarchy_variant)
        elif is_hierarchy and format_name == "json":
            write_hierarchy_json(
                target,
                fields,
                hierarchy_nodes,
                dataset_key=dataset_key,
                provenance=provenance or {},
            )
        elif is_hierarchy and format_name == "xlsx" and hierarchy_variant == "reporting":
            write_hierarchy_reporting_xlsx(target, fields, hierarchy_nodes)
        elif is_hierarchy and format_name == "xlsx":
            write_hierarchy_xlsx(target, fields, hierarchy_nodes, dataset_key)
        elif format_name == "xlsx":
            writer(target, fields, records, dataset_key)
        else:
            writer(target, fields, records)
        target.seek(0)
        suffix = (
            f"-{hierarchy_variant}"
            if is_hierarchy and (format_name == "csv" or hierarchy_variant == "reporting")
            else ""
        )
        extension = "zip" if format_name == "csv_zip" else format_name
        return ExportArtifact(target, media_type, extension, f"{dataset_key}{suffix}.{extension}")
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
