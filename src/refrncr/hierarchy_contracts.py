"""Immutable hierarchy content shared by concepts and complete version snapshots."""

from dataclasses import dataclass

from refrncr.ports import RecordValue


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
