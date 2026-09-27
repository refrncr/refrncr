"""Shared version classification; canonical keys are the value equality contract."""

from bisect import bisect_left
from dataclasses import fields as dataclass_fields

from refrncr.datatypes import get_datatype
from refrncr.diff_contracts import STATUSES, FieldChange, PropertyChange
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
    }

    def text(field, name):
        if field is None:
            return None
        value = getattr(field, name)
        if value is None:
            return None
        if name == "data_type":
            return get_datatype(value).label
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
                    "Added" if before is None else "Removed" if after is None else "Changed",
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
