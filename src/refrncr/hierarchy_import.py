"""Import the editable tabular hierarchy export into a Concept."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass

from refrncr.audit import record_audit
from refrncr.hierarchy import hierarchy_validation_errors
from refrncr.hierarchy_contracts import HierarchyNode
from refrncr.ports import RecordValue
from refrncr.record_import import (
    ImportValidationError,
    TransientImportStore,
    parse_source,
)
from refrncr.services import (
    DatasetFieldService,
    NotFoundError,
    StaleRecordError,
    ValidationError,
    _advance_concept,
    _require_concept,
    canonicalize_record_value,
)
from refrncr.upload_names import safe_upload_filename

STRUCTURAL_COLUMNS = ("Node ID", "Parent ID", "Name", "Parent")


@dataclass(frozen=True, slots=True)
class HierarchyImportRow:
    number: int
    node_id: str | None
    parent_id: str | None
    name: str
    values: tuple[RecordValue, ...]


@dataclass(frozen=True, slots=True)
class HierarchyImportPreview:
    expected_revision: int
    rows: tuple[HierarchyImportRow, ...]
    additions: int
    edits: int
    moves: int
    removals: int
    unchanged: int
    errors: tuple[str, ...] = ()

    @property
    def valid(self):
        return not self.errors

    @property
    def changed(self):
        return self.additions + self.edits + self.moves + self.removals


def _field_columns(fields):
    columns = []
    reserved = set(STRUCTURAL_COLUMNS)
    for field in fields:
        column = field.key
        if column in reserved:
            column = f"Field: {column}"
        while column in columns:
            column = f"Field: {column}"
        columns.append(column)
    return tuple(columns)


def _value_map(node):
    return {value.field_id: value.canonical_key for value in node.values}


class HierarchyImportService:
    """Bounded, token-bound hierarchy import with one serialized apply transaction."""

    def __init__(self, uow, authorization, store: TransientImportStore | None = None):
        self.uow = uow
        self.authorization = authorization
        self.store = store or TransientImportStore()

    def _target(self, actor_id, dataset_key):
        key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(key)
            if (
                dataset is None
                or dataset.kind != "hierarchy"
                or not self.authorization.datasets.can_maintain_dataset(
                    actor_id, dataset.id, uow=uow
                )
            ):
                raise NotFoundError("Dataset not found")
            _require_concept(uow, dataset.id)
            return dataset.id, dataset.revision

    @staticmethod
    def _parse_uuid(value, label):
        try:
            parsed = str(uuid.UUID(value))
        except (ValueError, AttributeError):
            raise ImportValidationError(f"{label} must be a valid Node ID") from None
        if parsed != value.casefold():
            raise ImportValidationError(f"{label} must be a canonical Node ID")
        return parsed

    def _preview_for(self, dataset_id, content, filename, expected_revision):
        if os.path.splitext(filename)[1].casefold() != ".xlsx":
            raise ImportValidationError("Upload the editable .xlsx hierarchy export")
        workbook = parse_source(filename, content)
        if workbook.kind != "xlsx" or "Data" not in workbook.worksheets:
            raise ImportValidationError("The workbook must contain a visible Data worksheet")
        source = parse_source(filename, content, "Data")
        with self.uow() as uow:
            fields = tuple(uow.dataset_fields.list_for_dataset(dataset_id))
            identifier = uow.dataset_business_keys.get(dataset_id)
            current = tuple(uow.hierarchy.list_for_dataset(dataset_id))
        by_id = {node.id: node for node in current}
        headers = tuple(source.headers)
        expected = (*STRUCTURAL_COLUMNS, *_field_columns(fields))
        errors = []
        if headers != expected:
            missing = [column for column in expected if column not in headers]
            unknown = [column for column in headers if column not in expected]
            if missing:
                errors.append("The Data worksheet is missing: " + ", ".join(missing))
            if unknown:
                errors.append(
                    "The Data worksheet contains unsupported columns: " + ", ".join(unknown)
                )
            if not missing and not unknown:
                errors.append("The Data worksheet columns are in the wrong order")
            return HierarchyImportPreview(expected_revision, (), 0, 0, 0, 0, 0, tuple(errors))

        rows = []
        seen_ids = set()
        for source_row in source.rows:
            values = source_row.values
            if source_row.formulas:
                errors.append(f"Row {source_row.number} contains a formula; use literal values")
                continue
            raw_id = values[0].strip()
            node_id = None
            if raw_id:
                node_id = self._parse_uuid(raw_id, f"Node ID in row {source_row.number}")
                if node_id in seen_ids:
                    errors.append(f"Node ID is duplicated in row {source_row.number}")
                seen_ids.add(node_id)
                if node_id not in by_id:
                    errors.append(f"Node ID in row {source_row.number} is not in this hierarchy")
            raw_parent = values[1].strip()
            parent_id = None
            if raw_parent:
                parent_id = self._parse_uuid(raw_parent, f"Parent ID in row {source_row.number}")
                if parent_id not in by_id:
                    errors.append(f"Parent ID in row {source_row.number} is not in this hierarchy")
            name = values[2].strip()
            if not name:
                errors.append(f"Name is required in row {source_row.number}")
            if parent_id is None and values[3].strip():
                errors.append(f"Root row {source_row.number} must have an empty Parent")
            # Parent ID is authoritative. The display-only Parent column can
            # legitimately reflect a renamed parent in the same workbook.
            parsed_values = []
            for index, field in enumerate(fields, 4):
                raw = values[index]
                if raw == "":
                    continue
                try:
                    canonical = canonicalize_record_value(field.data_type, raw)
                except ValidationError as error:
                    errors.append(f"Row {source_row.number}, {field.name}: {error}")
                    continue
                if canonical is not None:
                    value, key = canonical
                    parsed_values.append(
                        RecordValue(
                            field.id, field.key, field.data_type, value, key, field.is_unique
                        )
                    )
            rows.append(
                HierarchyImportRow(
                    source_row.number, node_id, parent_id, name, tuple(parsed_values)
                )
            )

        existing_rows = {row.node_id for row in rows if row.node_id is not None}
        removals = tuple(node for node in current if node.id not in existing_rows)
        temp_nodes = []
        for row in rows:
            temp_nodes.append(
                HierarchyNode(
                    row.node_id or f"new:{row.number}",
                    dataset_id,
                    row.name,
                    row.parent_id,
                    row.values,
                )
            )
        if not errors:
            errors.extend(
                hierarchy_validation_errors(
                    fields,
                    identifier,
                    tuple(temp_nodes),
                    publication=False,
                    allow_legacy_names=True,
                )
            )
            if any(node.parent_id is None for node in removals):
                errors.append("The existing root cannot be removed")
            parents = {node.parent_id for node in temp_nodes}
            for node in temp_nodes:
                if node.id.startswith("new:") and node.id in parents:
                    errors.append(
                        "A new node cannot be used as a parent; export stable Node IDs first"
                    )

        current_by_id = {node.id: node for node in current}
        additions = sum(row.node_id is None for row in rows)
        moves = sum(
            row.node_id is not None and current_by_id[row.node_id].parent_id != row.parent_id
            for row in rows
            if row.node_id in current_by_id
        )
        edits = sum(
            row.node_id is not None
            and (
                current_by_id[row.node_id].name != row.name
                or _value_map(current_by_id[row.node_id])
                != _value_map(
                    HierarchyNode(row.node_id, dataset_id, row.name, row.parent_id, row.values)
                )
            )
            for row in rows
            if row.node_id in current_by_id
        )
        unchanged = len(rows) - additions - edits - moves
        return HierarchyImportPreview(
            expected_revision,
            tuple(rows),
            additions,
            edits,
            moves,
            len(removals),
            unchanged,
            tuple(errors),
        )

    def begin(self, actor_id, session_id, dataset_key, filename, content):
        dataset_id, revision = self._target(actor_id, dataset_key)
        preview = self._preview_for(dataset_id, content, safe_upload_filename(filename), revision)
        if not preview.valid:
            raise ImportValidationError("; ".join(preview.errors))
        state = self.store.create(
            actor_id,
            session_id,
            dataset_id,
            safe_upload_filename(filename),
            content,
            "xlsx",
            concept_id=self._concept_id(dataset_id),
            mode="hierarchy",
        )
        digest = self._digest(preview)
        return self.store.update(
            state,
            worksheet="Data",
            mapping=tuple("hierarchy" for _ in preview.rows),
            schema_fingerprint=("hierarchy", revision),
            difference_digest=digest,
            confirmation=secrets.token_urlsafe(32),
            validation_valid=True,
            step=4,
        )

    def _concept_id(self, dataset_id):
        with self.uow() as uow:
            return _require_concept(uow, dataset_id).id

    @staticmethod
    def _digest(preview):
        value = repr(
            (
                preview.expected_revision,
                preview.rows,
                preview.additions,
                preview.edits,
                preview.moves,
                preview.removals,
            )
        )
        return hashlib.sha256(value.encode()).hexdigest()

    def state(self, actor_id, session_id, dataset_key, token):
        dataset_id, _revision = self._target(actor_id, dataset_key)
        state = self.store.get(token, actor_id, session_id, dataset_id)
        if state.mode != "hierarchy" or state.busy:
            raise NotFoundError("Import not found")
        return state

    def active(self, actor_id, session_id, dataset_key):
        dataset_id, _revision = self._target(actor_id, dataset_key)
        return self.store.active(actor_id, session_id, dataset_id)

    def cancel(self, actor_id, session_id, dataset_key, token):
        dataset_id, _revision = self._target(actor_id, dataset_key)
        self.store.cancel(token, actor_id, session_id, dataset_id)

    def revoke_session(self, session_id):
        self.store.revoke_session(session_id)

    def preview(self, actor_id, session_id, dataset_key, token):
        state = self.state(actor_id, session_id, dataset_key, token)
        _dataset_id, revision = self._target(actor_id, dataset_key)
        preview = self._preview_for(state.dataset_id, state.content, state.filename, revision)
        self.store.update(
            state,
            schema_fingerprint=("hierarchy", revision),
            difference_digest=self._digest(preview),
            validation_valid=preview.valid,
        )
        return preview

    def confirm(
        self, actor_id, session_id, dataset_key, token, *, confirmation, acknowledge_removals
    ):
        state = self.state(actor_id, session_id, dataset_key, token)
        if confirmation != state.confirmation:
            raise ImportValidationError("Explicit confirmation of proposed removals is required")
        claimed = self.store.claim(state)
        try:
            preview = self._preview_for(
                claimed.dataset_id,
                claimed.content,
                claimed.filename,
                claimed.schema_fingerprint[1],
            )
            if not preview.valid or self._digest(preview) != claimed.difference_digest:
                raise StaleRecordError("The import changed since validation")
            if preview.removals and not acknowledge_removals:
                raise ImportValidationError(
                    "Explicit confirmation of proposed removals is required"
                )
            result = self._apply(actor_id, claimed.dataset_id, preview)
        except Exception:
            self.store.finish(claimed)
            raise
        self.store.finish(claimed, imported_count=result["changed"], step=5)
        return result

    def _apply(self, actor_id, dataset_id, preview):
        with self.uow(write=True, serialized=True, translate_errors=True) as work:
            dataset = work.datasets.get(dataset_id, for_update=True)
            if dataset is None or dataset.kind != "hierarchy":
                raise NotFoundError("Dataset not found")
            if dataset.revision != preview.expected_revision:
                raise StaleRecordError("The hierarchy changed since validation")
            _require_concept(work, dataset.id)
            current = {node.id: node for node in work.hierarchy.list_for_dataset(dataset.id)}
            by_row = {row.node_id: row for row in preview.rows if row.node_id is not None}
            changed = 0
            for node_id, row in by_row.items():
                old = current[node_id]
                if old.name != row.name or old.parent_id != row.parent_id:
                    work.hierarchy.update(dataset.id, node_id, row.name, row.parent_id)
                    changed += 1
                if _value_map(old) != {value.field_id: value.canonical_key for value in row.values}:
                    work.hierarchy.replace_values(dataset.id, node_id, row.values)
                    if old.name == row.name and old.parent_id == row.parent_id:
                        changed += 1
            for row in preview.rows:
                if row.node_id is None:
                    node = work.hierarchy.create(dataset.id, row.name, row.parent_id)
                    if row.values:
                        work.hierarchy.replace_values(dataset.id, node.id, row.values)
                    changed += 1
            existing_ids = set(by_row)
            removals = [node for node in current.values() if node.id not in existing_ids]
            removed_ids = {node.id for node in removals}
            for node in removals:
                if node.parent_id not in removed_ids:
                    work.hierarchy.delete(dataset.id, node.id)
                    changed += 1
            if changed:
                _advance_concept(work, dataset)
                dataset.content_updated_at = work.now()
                work.concepts.record_mutation(dataset.id)
                record_audit(
                    work,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action="hierarchy.import_applied",
                    details={
                        "additions": preview.additions,
                        "edits": preview.edits,
                        "moves": preview.moves,
                        "removals": preview.removals,
                    },
                )
                work.flush()
            return {
                "changed": changed,
                "additions": preview.additions,
                "edits": preview.edits,
                "moves": preview.moves,
                "removals": preview.removals,
            }
