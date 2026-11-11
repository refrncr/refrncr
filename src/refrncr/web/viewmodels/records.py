"""Record-table presentation helpers independent of Flask and persistence."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol

from refrncr.datatypes import get_datatype


class RecordField(Protocol):
    key: str
    data_type: str


def record_value_formatter(
    fields: Sequence[RecordField],
    record_values: Iterable[Mapping[str, Any]],
) -> Callable[[RecordField, Any], str | None]:
    """Return a formatter that aligns decimal scales within each table column."""
    values_by_record = tuple(record_values)
    numeric_scales = {
        field.key: max(
            (
                max(0, -value.as_tuple().exponent)
                for values in values_by_record
                if isinstance((value := values.get(field.key)), Decimal)
            ),
            default=0,
        )
        for field in fields
        if field.data_type == "number"
    }

    def display(field: RecordField, value: Any) -> str | None:
        if field.data_type == "number" and isinstance(value, Decimal):
            scale = numeric_scales.get(field.key, max(0, -value.as_tuple().exponent))
            return f"{value:.{scale}f}"
        return get_datatype(field.data_type).text(value) if value is not None else None

    return display
