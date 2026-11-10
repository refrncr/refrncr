"""Pure hierarchy tree construction and table sorting."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from refrncr.hierarchy import hierarchy_sibling_sort_key
from refrncr.hierarchy_contracts import hierarchy_path_label


def hierarchy_presentation(view: Any) -> tuple[list[dict[str, Any]], dict[Any, list[Any]]]:
    """Arrange a hierarchy snapshot for presentation without changing it."""
    nodes = {node.id: node for node in view.hierarchy_nodes}
    children: dict[Any, list[Any]] = {}
    for node in nodes.values():
        children.setdefault(node.parent_id, []).append(node)
    for siblings in children.values():
        siblings.sort(key=hierarchy_sibling_sort_key)
    rows: list[dict[str, Any]] = []
    visited = set()

    def append_subtree(start: Any) -> None:
        pending = [(start, ())]
        while pending:
            node, ancestors = pending.pop()
            if node.id in visited:
                continue
            visited.add(node.id)
            ancestor_names = tuple(row["node"].name for row in ancestors)
            path = (*ancestor_names, node.name)
            rows.append(
                {
                    "node": node,
                    "depth": len(ancestors),
                    "ancestors": ancestor_names,
                    "ancestor_rows": ancestors,
                    "parent_name": (
                        nodes[node.parent_id].name if node.parent_id in nodes else None
                    ),
                    "path": hierarchy_path_label(path),
                    "leaf": not children.get(node.id),
                    "values": {
                        value.field_key: value.value
                        for value in node.values
                        if any(
                            field.id == value.field_id and field.data_type == value.data_type
                            for field in view.fields
                        )
                    },
                }
            )
            pending.extend(
                (child, (*ancestors, rows[-1])) for child in reversed(children.get(node.id, ()))
            )

    for node in children.get(None, ()):
        append_subtree(node)
    # Invalid disconnected structure remains visible while its validation is resolved.
    for node in nodes.values():
        if node.id not in visited:
            append_subtree(node)
    return rows, children


def sort_hierarchy_rows(
    rows: Sequence[dict[str, Any]],
    sort_key: str,
    key_field: Any | None,
    *,
    descending: bool = False,
) -> list[dict[str, Any]]:
    """Sort flattened hierarchy rows using the existing table ordering contract."""

    def row_sort_value(row: dict[str, Any]) -> tuple[int, Any]:
        if sort_key == "name":
            return (1, row["node"].name.casefold())
        if sort_key == "parent":
            return (1, (row["parent_name"] or "").casefold())
        value = row["values"].get(key_field.key) if key_field else None
        if value is None:
            return (0, "")
        if key_field and key_field.data_type in {"integer", "number", "float"}:
            return (1, value)
        return (2, str(value).casefold())

    return sorted(
        rows,
        key=lambda row: (row_sort_value(row), row["path"].casefold()),
        reverse=descending,
    )
