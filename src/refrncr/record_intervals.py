"""Derived record-validity intervals over immutable publication snapshots."""

from dataclasses import dataclass
from datetime import date

from refrncr.datatypes import get_datatype
from refrncr.published_contracts import record_identifier


@dataclass(frozen=True, slots=True)
class RecordInterval:
    identifier: str
    values: dict[str, object]
    valid_from: date
    valid_to: date | None
    version: int
    sort_key: tuple


@dataclass(frozen=True, slots=True)
class IntervalResult:
    rows: tuple[RecordInterval, ...]
    field_keys: tuple[str, ...]
    total: int


@dataclass(frozen=True, slots=True)
class HistoryRead:
    publications: tuple[object, ...]
    records: tuple[RecordInterval, ...]
    field_keys: tuple[str, ...]
    total: int
    not_modified: bool = False


def overlaps(start: date, end: date | None, lower: date | None, upper: date | None) -> bool:
    if end is not None and end <= start:
        return False
    return (upper is None or start < upper) and (end is None or lower is None or end > lower)


def _visible_values(record):
    result = {}
    for value in record.values:
        rendered = value.value
        if value.data_type in {"date", "time", "datetime", "timestamp"}:
            rendered = get_datatype(value.data_type).text(rendered)
        result[value.field_key] = rendered
    return result


def _sort_key(record, fields):
    values = {value.field_key: value for value in record.values}
    parts = []
    for field in sorted((field for field in fields if field.is_identifier), key=lambda f: f.key):
        value = values.get(field.key)
        parts.append(
            (1, None) if value is None else get_datatype(value.data_type).ordering_key(value.value)
        )
    return tuple(parts)


def derive_intervals(snapshots, *, lower=None, upper=None, offset=0, limit=50):
    """Merge equal visible states, then filter without clipping their real periods."""
    active = {}
    completed = []
    field_keys = []
    for state, fields, records in snapshots:
        if state.valid_until is not None and state.valid_until <= state.version.effective_date:
            continue
        if overlaps(state.version.effective_date, state.valid_until, lower, upper):
            for field in fields:
                if field.key not in field_keys:
                    field_keys.append(field.key)
        seen = set()
        for record in records:
            identifier = record_identifier(record, fields)
            seen.add(identifier)
            values = _visible_values(record)
            prior = active.get(identifier)
            if prior is not None and prior.values == values:
                continue
            if prior is not None:
                completed.append(
                    RecordInterval(
                        prior.identifier,
                        prior.values,
                        prior.valid_from,
                        state.version.effective_date,
                        prior.version,
                        prior.sort_key,
                    )
                )
            active[identifier] = RecordInterval(
                identifier,
                values,
                state.version.effective_date,
                None,
                state.version.number,
                _sort_key(record, fields),
            )
        for identifier in tuple(active.keys() - seen):
            prior = active.pop(identifier)
            completed.append(
                RecordInterval(
                    prior.identifier,
                    prior.values,
                    prior.valid_from,
                    state.version.effective_date,
                    prior.version,
                    prior.sort_key,
                )
            )
    completed.extend(active.values())
    rows = [row for row in completed if overlaps(row.valid_from, row.valid_to, lower, upper)]
    rows.sort(key=lambda row: (row.sort_key, row.valid_from, row.version))
    total = len(rows)
    keys = tuple(field_keys)
    rows = [
        RecordInterval(
            row.identifier,
            {key: row.values.get(key) for key in keys},
            row.valid_from,
            row.valid_to,
            row.version,
            row.sort_key,
        )
        for row in rows[offset : offset + limit]
    ]
    return IntervalResult(tuple(rows), keys, total)
