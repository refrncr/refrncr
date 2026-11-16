"""Immutable hierarchy content shared by concepts and complete version snapshots."""

from dataclasses import dataclass

from refrncr.hierarchies.errors import HierarchyValidationError
from refrncr.ports import RecordValue


def validate_hierarchy_scope(scope, kind):
    if kind != "hierarchy":
        if scope is not None:
            raise HierarchyValidationError("Field scope is only available for hierarchy datasets")
        return None
    if scope not in {"leaves", "all_nodes"}:
        raise HierarchyValidationError("Choose Leaves only or All nodes for the field scope")
    return scope


def hierarchy_sibling_sort_key(node):
    return node.name.casefold(), node.name, node.id


def hierarchy_order(nodes):
    """Return deterministic depth-first order with stable sibling sorting."""
    by_parent = {}
    for node in nodes:
        by_parent.setdefault(node.parent_id, []).append(node)
    for siblings in by_parent.values():
        siblings.sort(key=hierarchy_sibling_sort_key)
    ordered = []
    pending = list(reversed(by_parent.get(None, ())))
    while pending:
        node = pending.pop()
        ordered.append(node)
        pending.extend(reversed(by_parent.get(node.id, ())))
    ordered.extend(node for node in nodes if node not in ordered)
    return tuple(ordered)


def hierarchy_paths(nodes):
    """Return finite node paths without recursion."""
    nodes = tuple(nodes)
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


def applicable(field, node, parent_ids):
    return field.hierarchy_scope == "all_nodes" or node.id not in parent_ids


@dataclass(frozen=True, slots=True)
class HierarchyNode:
    id: str
    dataset_id: str
    name: str
    parent_id: str | None
    values: tuple[RecordValue, ...] = ()
    revision: int = 1


def hierarchy_path_label(names):
    """Format a hierarchy path with the shared visible path separator."""
    return " / ".join(names)


def hierarchy_node_paths(nodes):
    """Return node paths without recursion, including finite labels for invalid graphs."""
    nodes = tuple(nodes)
    by_id = {node.id: node for node in nodes}
    paths = {}
    for node in nodes:
        pending, visited = [], set()
        current = node.id
        while current is not None and current not in paths:
            if current in visited or current not in by_id:
                break
            visited.add(current)
            ancestor = by_id[current]
            pending.append(ancestor)
            current = ancestor.parent_id
        prefix = paths.get(current, "Invalid hierarchy") if current is not None else ""
        for ancestor in reversed(pending):
            prefix = hierarchy_path_label((prefix, ancestor.name)) if prefix else ancestor.name
            paths[ancestor.id] = prefix
    return paths
