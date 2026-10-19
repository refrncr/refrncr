"""Standalone hierarchy behavior; node identity is independent of leaf identifiers."""

from dataclasses import replace

from refrncr.audit import record_audit
from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.hierarchy_contracts import hierarchy_node_paths
from refrncr.ports import RecordValue, StoredRecord
from refrncr.record_identity import RecordIdentifierError, encode_record_identifier
from refrncr.services import (
    DatasetFieldService,
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    StaleRecordError,
    ValidationError,
    _advance_concept,
    _now,
    _require_concept,
    _valid_identity,
    canonicalize_record_value,
    normalize_dataset_field_name,
    normalize_dataset_name,
    validate_dataset_description,
    validate_dataset_field_flag,
    validate_dataset_field_key,
    validate_dataset_field_type,
    validate_dataset_key,
    validate_field_metadata,
)
from refrncr.version_contracts import SnapshotContent, SnapshotField


def validate_hierarchy_scope(scope, kind):
    if kind != "hierarchy":
        if scope is not None:
            raise ValidationError("Field scope is only available for hierarchy tables")
        return None
    if scope not in {"leaves", "all_nodes"}:
        raise ValidationError("Choose Leaves only or All nodes for the field scope")
    return scope


def _node_name(name):
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 200:
        raise ValidationError("Node name must contain between 1 and 200 characters")
    return name


def applicable(field, node, parent_ids):
    return field.hierarchy_scope == "all_nodes" or node.id not in parent_ids


def hierarchy_leaf_records(nodes, fields):
    parents = {node.parent_id for node in nodes}
    by_id = {field.id: field for field in fields}
    return tuple(
        StoredRecord(
            node.id,
            node.dataset_id,
            tuple(
                replace(value, is_unique=by_id[value.field_id].is_unique)
                for value in node.values
                if value.field_id in by_id
            ),
            node.revision,
        )
        for node in nodes
        if node.parent_id is not None and node.id not in parents
    )


def _structure_errors(nodes, *, publication, allow_legacy_names=False):
    errors = []
    by_id = {node.id: node for node in nodes}
    roots = [node for node in nodes if node.parent_id is None]
    if len(roots) != 1:
        errors.append("The hierarchy must have exactly one root")
    if publication and len(nodes) < 2:
        errors.append("Add at least one child beneath the root before publication")
    if len(by_id) != len(nodes):
        errors.append("Hierarchy node identities must be unique")
    names = set()
    children = {}
    for node in nodes:
        try:
            if _node_name(node.name) != node.name:
                errors.append("Node names must not start or end with whitespace")
        except ValidationError as error:
            errors.append(str(error))
        marker = (node.parent_id, node.name) if allow_legacy_names else node.name
        if marker in names:
            errors.append(
                f"Sibling names must be unique: {node.name}"
                if allow_legacy_names
                else f"Node names must be unique within the hierarchy: {node.name}"
            )
        names.add(marker)
        if node.parent_id is not None and node.parent_id not in by_id:
            errors.append(f"Choose an existing parent for {node.name}")
        children.setdefault(node.parent_id, []).append(node.id)
    reached, pending = set(), [node.id for node in roots]
    while pending:
        node_id = pending.pop()
        if node_id in reached:
            errors.append("Move nodes so that the hierarchy contains no cycles")
            break
        reached.add(node_id)
        pending.extend(children.get(node_id, ()))
    if len(reached) != len(nodes):
        errors.append("Connect every node to the root without creating a cycle")
    return errors


def hierarchy_validation_errors(
    fields, identifier, nodes, *, publication=True, allow_legacy_names=False
):
    """Validate applicable content; inactive values are retained without constraints."""
    errors = _structure_errors(
        nodes, publication=publication, allow_legacy_names=allow_legacy_names
    )
    paths = hierarchy_node_paths(nodes)
    parents = {node.parent_id for node in nodes}
    by_id = {field.id: field for field in fields}
    by_key = {field.key: field for field in fields}
    if any(field.hierarchy_scope not in {"leaves", "all_nodes"} for field in fields):
        errors.append("Every hierarchy field must have an explicit scope")
    keys = identifier.field_keys
    if len(set(keys)) != len(keys) or any(
        key not in by_key
        or not by_key[key].is_required
        or not get_datatype(by_key[key].data_type).identifier_eligible
        for key in keys
    ):
        errors.append("Invalid Record identifier configuration")
    seen_unique, seen_identifiers = {}, {}
    for node in nodes:
        label = paths[node.id]
        values = {value.field_id: value for value in node.values}
        if len(values) != len(node.values) or any(value_id not in by_id for value_id in values):
            errors.append(f"Invalid stored field values for {label}")
        for field in fields:
            if not applicable(field, node, parents):
                continue
            value = values.get(field.id)
            if value is None:
                if field.is_required:
                    errors.append(f"{label}: {field.name} requires a value")
                continue
            try:
                if value.data_type != field.data_type or value.field_key != field.key:
                    raise DatatypeError("Mismatched field")
                get_datatype(field.data_type).from_key(value.canonical_key)
            except (DatatypeError, ValueError):
                errors.append(f"{label}: {field.name} has an invalid value")
                continue
            if field.is_unique:
                marker = field.id, value.canonical_key
                if marker in seen_unique:
                    errors.append(
                        f"{label}: {field.name} must be unique; also used by {seen_unique[marker]}"
                    )
                else:
                    seen_unique[marker] = label
        if keys and node.parent_id is not None and node.id not in parents:
            if any(key not in by_key or by_key[key].id not in values for key in keys):
                continue  # Required-field errors provide the actionable missing components.
            ordered_keys = tuple(sorted(keys))
            marker = tuple(values[by_key[key].id].canonical_key for key in ordered_keys)
            try:
                encode_record_identifier(
                    dict(zip(ordered_keys, marker, strict=True)), expected_keys=ordered_keys
                )
            except RecordIdentifierError as error:
                errors.append(f"{label}: {error}")
            if marker in seen_identifiers:
                errors.append(
                    f"{label}: Record identifier must be unique among leaves; also used by "
                    f"{seen_identifiers[marker]}"
                )
            else:
                seen_identifiers[marker] = label
    return tuple(dict.fromkeys(errors))


def hierarchy_snapshot(uow, dataset, *, validate=True):
    """Capture complete content and the leaf projection in the caller's transaction."""
    fields = uow.dataset_fields.list_for_dataset(dataset.id)
    identifier = uow.dataset_business_keys.get(dataset.id)
    nodes = uow.hierarchy.list_for_dataset(dataset.id)
    validate_dataset_key(dataset.key)
    normalize_dataset_name(dataset.name)
    validate_dataset_description(dataset.description)
    if (
        len({field.id for field in fields}) != len(fields)
        or len({field.key for field in fields}) != len(fields)
        or sorted(field.display_position for field in fields) != list(range(1, len(fields) + 1))
    ):
        raise ValidationError("Invalid Field configuration")
    for field in fields:
        if field.dataset_id != dataset.id or not _valid_identity(field.id):
            raise ValidationError("Invalid Field identity")
        validate_dataset_field_key(field.key)
        normalize_dataset_field_name(field.name)
        validate_dataset_field_type(field.data_type)
        validate_dataset_field_flag(field.is_required, "Required")
        validate_dataset_field_flag(field.is_unique, "Unique")
        validate_field_metadata(field.definition, field.unit)
        validate_hierarchy_scope(field.hierarchy_scope, "hierarchy")
    if any(
        node.dataset_id != dataset.id
        or not _valid_identity(node.id)
        or (node.parent_id is not None and not _valid_identity(node.parent_id))
        for node in nodes
    ):
        raise ValidationError("Invalid hierarchy node identity")
    snapshot_fields = tuple(
        SnapshotField(
            field.id,
            dataset.id,
            field.key,
            field.name,
            field.data_type,
            field.is_required,
            field.is_unique,
            field.display_position,
            field.key in identifier.field_keys,
            field.definition,
            field.unit,
            field.mapping_role,
            field.source_field_key,
            field.hierarchy_scope,
        )
        for field in fields
    )
    if validate and (errors := hierarchy_validation_errors(snapshot_fields, identifier, nodes)):
        raise ValidationError("; ".join(errors))
    return SnapshotContent(
        dataset.name,
        dataset.description,
        snapshot_fields,
        hierarchy_leaf_records(nodes, snapshot_fields),
        nodes,
    )


class HierarchyService:
    """Hierarchy writers share the Dataset authorization, lock and concept boundary."""

    def __init__(self, uow, authorization, *, versions=None):
        self.uow = uow
        self.authorization = authorization
        self.versions = versions

    def _target(self, work, actor_id, dataset_key, expected_revision):
        key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        dataset = work.datasets.find_by_key(key, for_update=True)
        if (
            dataset is None
            or dataset.kind != "hierarchy"
            or not self.authorization.datasets.can_maintain_dataset(actor_id, dataset.id, uow=work)
        ):
            raise NotFoundError("Dataset not found")
        _require_concept(work, dataset.id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValidationError("Expected revision must be a positive integer")
        if expected_revision != dataset.revision:
            raise StaleRecordError("Hierarchy changed since this form was opened")
        return dataset

    @staticmethod
    def _changed(work, dataset, actor_id, action):
        _advance_concept(work, dataset)
        dataset.content_updated_at = _now()
        work.concepts.record_mutation(dataset.id)
        record_audit(
            work,
            actor_user_id=actor_id,
            object_type="dataset",
            object_id=dataset.id,
            action=action,
        )
        work.flush()

    def create_node(self, actor_id, dataset_key, name, parent_id=None, *, expected_revision):
        with self.uow(write=True, serialized=True) as work:
            dataset = self._target(work, actor_id, dataset_key, expected_revision)
            name = _node_name(name)
            nodes = work.hierarchy.list_for_dataset(dataset.id)
            if parent_id is None:
                if nodes:
                    raise ValidationError("The hierarchy already has a root; choose a parent")
            elif not any(node.id == parent_id for node in nodes):
                raise ValidationError("Choose a parent in this hierarchy")
            if any(node.name == name for node in nodes):
                raise ValidationError("A node in this hierarchy already has this name")
            node = work.hierarchy.create(dataset.id, name, parent_id)
            self._changed(work, dataset, actor_id, "hierarchy.node_created")
            return node

    def update_node(self, actor_id, dataset_key, node_id, name, parent_id, *, expected_revision):
        with self.uow(write=True, serialized=True) as work:
            dataset = self._target(work, actor_id, dataset_key, expected_revision)
            nodes = work.hierarchy.list_for_dataset(dataset.id)
            node = next((item for item in nodes if item.id == node_id), None)
            if node is None:
                raise NotFoundError("Node not found")
            if (node.parent_id is None) != (parent_id is None):
                raise ValidationError("Keep the existing root and choose a parent for other nodes")
            name = _node_name(name)
            if name != node.name and any(
                item.id != node_id and item.name == name for item in nodes
            ):
                raise ValidationError("A node in this hierarchy already has this name")
            proposed = tuple(
                replace(item, name=name, parent_id=parent_id) if item.id == node_id else item
                for item in nodes
            )
            # Previously published hierarchies may have duplicate names under
            # different parents. Permit independent repairs and unchanged-name
            # moves while still rejecting new conflicts and invalid structure.
            if errors := _structure_errors(proposed, publication=False, allow_legacy_names=True):
                raise ValidationError("; ".join(errors))
            if node.name == name and node.parent_id == parent_id:
                return node
            updated = work.hierarchy.update(dataset.id, node_id, name, parent_id)
            # Applicability is derived. A move may restore conflicting values; keep them
            # in this concept, and let the shared validator prevent publication.
            self._changed(work, dataset, actor_id, "hierarchy.node_updated")
            return updated

    def _validated_values(
        self, work, dataset, target_node, candidate_nodes, fields, submitted, parent_ids
    ):
        active = [field for field in fields if applicable(field, target_node, parent_ids)]
        if not isinstance(submitted, dict) or set(submitted) - {field.key for field in active}:
            raise ValidationError("Only applicable fields may be edited for this node")
        active_ids = {field.id for field in active}
        values = [value for value in target_node.values if value.field_id not in active_ids]
        errors = {}
        identifier = work.dataset_business_keys.get(dataset.id)
        for field in active:
            try:
                canonical = canonicalize_record_value(field.data_type, submitted.get(field.key))
            except ValidationError as error:
                errors[field.key] = str(error)
                continue
            if canonical is None:
                if field.is_required or field.key in identifier.field_keys:
                    errors[field.key] = "A value is required"
                continue
            value, key = canonical
            if field.is_unique and any(
                other.id != target_node.id
                and applicable(field, other, parent_ids)
                and any(
                    item.field_id == field.id and item.canonical_key == key for item in other.values
                )
                for other in candidate_nodes
            ):
                errors[field.key] = "This value must be unique within the field scope"
            values.append(
                RecordValue(field.id, field.key, field.data_type, value, key, field.is_unique)
            )
        if errors:
            raise RecordValidationError(errors)
        if (
            identifier.field_keys
            and target_node.parent_id is not None
            and target_node.id not in parent_ids
        ):
            by_key = {value.field_key: value.canonical_key for value in values}
            marker = tuple(by_key.get(key) for key in identifier.field_keys)
            if any(component is None for component in marker):
                raise RecordIdentifierError
            encode_record_identifier(
                dict(zip(identifier.field_keys, marker, strict=True)),
                expected_keys=identifier.field_keys,
            )
            for other in candidate_nodes:
                if other.id == target_node.id or other.parent_id is None or other.id in parent_ids:
                    continue
                other_values = {value.field_key: value.canonical_key for value in other.values}
                if marker == tuple(other_values.get(key) for key in identifier.field_keys):
                    raise RecordConflictError("Record identifier already exists on another leaf")
        return tuple(sorted(values, key=lambda value: value.field_id))

    def update_node_and_values(
        self, actor_id, dataset_key, node_id, name, parent_id, submitted, *, expected_revision
    ):
        """Update a node and its applicable values in one authorized transaction."""
        with self.uow(write=True, serialized=True) as work:
            dataset = self._target(work, actor_id, dataset_key, expected_revision)
            nodes = work.hierarchy.list_for_dataset(dataset.id)
            node = next((item for item in nodes if item.id == node_id), None)
            if node is None:
                raise NotFoundError("Node not found")
            if (node.parent_id is None) != (parent_id is None):
                raise ValidationError("Keep the existing root and choose a parent for other nodes")
            name = _node_name(name)
            if name != node.name and any(
                item.id != node_id and item.name == name for item in nodes
            ):
                raise ValidationError("A node in this hierarchy already has this name")
            proposed = tuple(
                replace(item, name=name, parent_id=parent_id) if item.id == node_id else item
                for item in nodes
            )
            if errors := _structure_errors(proposed, publication=False, allow_legacy_names=True):
                raise ValidationError("; ".join(errors))

            proposed_node = next(item for item in proposed if item.id == node_id)
            parent_ids = {item.parent_id for item in proposed}
            fields = work.dataset_fields.list_for_dataset(dataset.id)
            values = self._validated_values(
                work, dataset, proposed_node, proposed, fields, submitted, parent_ids
            )
            values_changed = {value.field_id: value.canonical_key for value in values} != {
                value.field_id: value.canonical_key for value in node.values
            }
            structure_changed = node.name != name or node.parent_id != parent_id
            if not structure_changed and not values_changed:
                return node
            if structure_changed:
                work.hierarchy.update(dataset.id, node_id, name, parent_id)
            if values_changed:
                work.hierarchy.replace_values(dataset.id, node.id, values)
            self._changed(work, dataset, actor_id, "hierarchy.node_updated")
            return work.hierarchy.get(dataset.id, node.id)

    def delete_subtree(self, actor_id, dataset_key, node_id, *, expected_revision):
        """Delete one non-root node and every descendant in the same transaction."""
        with self.uow(write=True, serialized=True) as work:
            dataset = self._target(work, actor_id, dataset_key, expected_revision)
            nodes = work.hierarchy.list_for_dataset(dataset.id)
            node = next((item for item in nodes if item.id == node_id), None)
            if node is None:
                raise NotFoundError("Node not found")
            if node.parent_id is None:
                raise ValidationError("The root node cannot be deleted")

            children = {}
            for item in nodes:
                children.setdefault(item.parent_id, []).append(item.id)
            affected, pending = set(), [node.id]
            while pending:
                current = pending.pop()
                if current in affected:
                    continue
                affected.add(current)
                pending.extend(children.get(current, ()))

            work.hierarchy.delete(dataset.id, node.id)
            self._changed(work, dataset, actor_id, "hierarchy.subtree_deleted")
            return len(affected)

    def update_values(self, actor_id, dataset_key, node_id, submitted, *, expected_revision):
        with self.uow(write=True, serialized=True) as work:
            dataset = self._target(work, actor_id, dataset_key, expected_revision)
            nodes = work.hierarchy.list_for_dataset(dataset.id)
            node = next((item for item in nodes if item.id == node_id), None)
            if node is None:
                raise NotFoundError("Node not found")
            fields = work.dataset_fields.list_for_dataset(dataset.id)
            parents = {item.parent_id for item in nodes}
            values = self._validated_values(work, dataset, node, nodes, fields, submitted, parents)
            if {v.field_id: v.canonical_key for v in values} == {
                v.field_id: v.canonical_key for v in node.values
            }:
                return node
            work.hierarchy.replace_values(dataset.id, node.id, values)
            self._changed(work, dataset, actor_id, "hierarchy.values_updated")
            return work.hierarchy.get(dataset.id, node.id)
