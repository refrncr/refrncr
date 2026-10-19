"""Shared version classification; canonical keys are the value equality contract."""

from bisect import bisect_left
from dataclasses import fields as dataclass_fields

from refrncr.change_status import ChangeStatus
from refrncr.datatypes import get_datatype
from refrncr.diff_contracts import STATUSES, FieldChange, PropertyChange
from refrncr.hierarchy_contracts import hierarchy_node_paths
from refrncr.services import ValidationError
from refrncr.version_contracts import SnapshotField, SummaryCounts


def relative_moves(before, after):
    """Complement of deterministic LIS among surviving identities, O(n log n).

    In a tie, keep the smallest final old position at each subsequence length.
    Inserting/removing an identity never moves its surviving neighbours.
    """
    old = {identity: position for position, identity in enumerate(before)}
    if len(old) != len(before) or len(set(after)) != len(after):
        raise ValidationError("Invalid Field order")
    surviving = [identity for identity in after if identity in old]
    tails, ends, previous = [], [], []
    for index, identity in enumerate(surviving):
        position = bisect_left(tails, old[identity])
        previous.append(ends[position - 1] if position else -1)
        if position == len(tails):
            tails.append(old[identity])
            ends.append(index)
        else:
            tails[position], ends[position] = old[identity], index
    retained = set()
    index = ends[-1] if ends else -1
    while index != -1:
        retained.add(surviving[index])
        index = previous[index]
    return set(surviving) - retained


def classify_fields(current, previous):
    for collection in (current, previous):
        if (
            len({f.id for f in collection}) != len(collection)
            or len({f.key for f in collection}) != len(collection)
            or sorted(f.display_position for f in collection) != list(range(1, len(collection) + 1))
        ):
            raise ValidationError("Invalid versioned Field configuration")
        for field in collection:
            get_datatype(field.data_type)
    new, old = ({f.id: f for f in collection} for collection in (current, previous))
    moved = relative_moves(
        [f.id for f in sorted(previous, key=lambda f: f.display_position)],
        [f.id for f in sorted(current, key=lambda f: f.display_position)],
    )

    def positions(collection, included):
        result, preceding = {}, None
        for field in sorted(collection, key=lambda f: f.display_position):
            if field.id in included:
                result[field.id] = "First Field" if preceding is None else f"After {preceding}"
                preceding = field.name
        return result

    old_positions = positions(previous, old.keys() & new.keys())
    new_positions = positions(current, old.keys() & new.keys())
    old_all = positions(previous, old)
    new_all = positions(current, new)
    labels = {
        "key": "Field key",
        "name": "Name",
        "definition": "Definition",
        "unit": "Unit",
        "data_type": "Datatype",
        "is_required": "Required",
        "is_unique": "Unique",
        "is_identifier": "Record identifier",
        "mapping_role": "Mapping role",
        "source_field_key": "Source field",
        "hierarchy_scope": "Applicability",
    }

    def text(field, name):
        if field is None:
            return None
        value = getattr(field, name)
        if value is None:
            return None
        if name == "data_type":
            return get_datatype(value).label
        if name == "hierarchy_scope":
            return {"leaves": "Leaves only", "all_nodes": "All nodes"}.get(value, value)
        return ("Yes" if value else "No") if isinstance(value, bool) else str(value)

    result = []
    for identity in new.keys() | old.keys():
        before, after = old.get(identity), new.get(identity)
        properties = [
            PropertyChange(labels[name], text(before, name), text(after, name))
            for name in labels
            if before is None or after is None or getattr(before, name) != getattr(after, name)
        ]
        if identity in moved:
            properties.append(
                PropertyChange("Relative order", old_positions[identity], new_positions[identity])
            )
        elif before is None or after is None:
            properties.append(PropertyChange("Order", old_all.get(identity), new_all.get(identity)))
        if properties:
            result.append(
                FieldChange(
                    identity,
                    (after or before).name,
                    ChangeStatus.ADDED
                    if before is None
                    else ChangeStatus.REMOVED
                    if after is None
                    else ChangeStatus.CHANGED,
                    tuple(properties),
                    identity in moved,
                    bool(before and after and before.data_type != after.data_type),
                )
            )
    # Guard new snapshot properties against an accidentally incomplete classifier.
    assert {f.name for f in dataclass_fields(SnapshotField)} == set(labels) | {
        "id",
        "dataset_id",
        "display_position",
    }
    return tuple(
        sorted(
            result, key=lambda item: (STATUSES.index(item.status), item.name.casefold(), item.id)
        )
    )


def summary_counts(fields, records):
    return SummaryCounts(
        *(sum(field.status == status for field in fields) for status in STATUSES),
        *records,
    )


def classify_hierarchy(current, previous, current_fields, previous_fields):
    """Compare the complete tree and retained values by stable node identity."""
    new, old = ({node.id: node for node in nodes} for nodes in (current, previous))
    new_fields, old_fields = (
        {field.id: field for field in fields} for fields in (current_fields, previous_fields)
    )
    new_parents, old_parents = (
        {node.parent_id for node in nodes if node.parent_id is not None}
        for nodes in (current, previous)
    )
    new_paths, old_paths = (hierarchy_node_paths(nodes) for nodes in (current, previous))

    def role(node, parents):
        if node is None:
            return None
        return "Root" if node.parent_id is None else "Branch" if node.id in parents else "Leaf"

    def rendered(value):
        if value is None:
            return None
        datatype = get_datatype(value.data_type)
        return datatype.text(datatype.from_key(value.canonical_key))

    changes = []
    for identity in new.keys() | old.keys():
        before, after = old.get(identity), new.get(identity)
        properties = []
        for label, left, right in (
            ("Name", before.name if before else None, after.name if after else None),
            (
                "Parent",
                old_paths.get(before.parent_id) if before else None,
                new_paths.get(after.parent_id) if after else None,
            ),
            (
                "Node type",
                role(before, old_parents),
                role(after, new_parents),
            ),
        ):
            if label == "Parent" and before and after and before.parent_id == after.parent_id:
                continue
            if left != right:
                properties.append(PropertyChange(label, left, right))
        left_values = {value.field_id: value for value in before.values} if before else {}
        right_values = {value.field_id: value for value in after.values} if after else {}
        for field_id in sorted(left_values.keys() | right_values.keys()):
            left, right = left_values.get(field_id), right_values.get(field_id)
            field = new_fields.get(field_id) or old_fields.get(field_id)
            if field is None:
                raise ValidationError("Invalid hierarchy Field configuration")
            left_key = (left.data_type, left.canonical_key) if left else None
            right_key = (right.data_type, right.canonical_key) if right else None
            if left_key != right_key:
                properties.append(PropertyChange(field.name, rendered(left), rendered(right)))
            if left is not None and right is not None:
                old_active = (
                    identity not in old_parents
                    or old_fields[field_id].hierarchy_scope == "all_nodes"
                )
                new_active = (
                    identity not in new_parents
                    or new_fields[field_id].hierarchy_scope == "all_nodes"
                )
                if old_active != new_active:
                    properties.append(
                        PropertyChange(
                            f"{field.name} applicability",
                            "Active" if old_active else "Retained",
                            "Active" if new_active else "Retained",
                        )
                    )
        if properties:
            changes.append(
                FieldChange(
                    identity,
                    new_paths.get(identity) or old_paths[identity],
                    ChangeStatus.ADDED
                    if before is None
                    else ChangeStatus.REMOVED
                    if after is None
                    else ChangeStatus.CHANGED,
                    tuple(properties),
                    bool(before and after and before.parent_id != after.parent_id),
                )
            )
    return tuple(
        sorted(
            changes, key=lambda item: (STATUSES.index(item.status), item.name.casefold(), item.id)
        )
    )
