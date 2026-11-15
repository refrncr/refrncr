"""Period-history export generation for Dataset records."""

import csv
import io
import tempfile

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .export import SPOOL_MEMORY_LIMIT, ExportArtifact, ExportGenerationError


def _csv_string(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _worksheet_title(value: str) -> str:
    cleaned = "".join("_" if character in "[]:*?/\\" else character for character in value)
    cleaned = cleaned.strip("'")[:31]
    return cleaned or "Dataset"


def _history_headers(field_keys):
    headers = list(field_keys)
    metadata = []
    for key in ("valid_from", "valid_to", "version"):
        metadata.append(key if key not in headers else f"{key} (period)")
    return headers, tuple(metadata)


def generate_history_export(format_name, field_keys, rows, dataset_key, *, open_end=False):
    """Write the same flattened interval rows exposed by the history API."""
    if format_name not in {"csv", "xlsx"}:
        raise ExportGenerationError("Period exports are available as CSV or Excel")
    target = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_LIMIT, mode="w+b")
    fields, metadata = _history_headers(field_keys)
    header = [*fields, *metadata]

    def values(row):
        end = "9999-12-31" if open_end and row.valid_to is None else row.valid_to
        return [
            *(row.values.get(key) for key in fields),
            row.valid_from,
            end,
            row.version,
        ]

    try:
        if format_name == "csv":
            target.write(b"\xef\xbb\xbf")
            text = io.TextIOWrapper(target, encoding="utf-8", newline="", write_through=True)
            writer = csv.writer(text, delimiter=",", lineterminator="\r\n")
            writer.writerow(header)
            for row in rows:
                writer.writerow(
                    [
                        _csv_string(value) if isinstance(value, str) else value
                        for value in values(row)
                    ]
                )
            text.flush()
            text.detach()
            media_type = "text/csv; charset=utf-8"
        else:
            workbook = Workbook(write_only=True)
            try:
                sheet = workbook.create_sheet(title=_worksheet_title(dataset_key))
                sheet.append(header)
                for row in rows:
                    cells = []
                    for value in values(row):
                        if isinstance(value, str) and value.lstrip().startswith(
                            ("=", "+", "-", "@")
                        ):
                            cell = WriteOnlyCell(sheet, value=value)
                            cell.data_type = "s"
                            cells.append(cell)
                        else:
                            cells.append(value)
                    sheet.append(cells)
                sheet.close()
                workbook.save(target)
            finally:
                workbook.close()
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        target.seek(0)
        return ExportArtifact(
            target,
            media_type,
            format_name,
            f"{dataset_key}-history.{format_name}",
        )
    except Exception:
        target.close()
        raise
