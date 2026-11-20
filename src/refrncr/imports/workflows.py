from __future__ import annotations

import secrets

from refrncr.audit import record_audit
from refrncr.datasets import RecordConflictError
from refrncr.datasets.fields import DatasetFieldService
from refrncr.datasets.records import DatasetRecordService
from refrncr.domain import advance_concept
from refrncr.errors import ValidationError
from refrncr.imports.errors import (
    ImportNotFoundError,
    ImportValidationError,
    SourceFormatComponentsError,
)
from refrncr.imports.parsing import parse_source
from refrncr.ports import PersistenceConflictError
from refrncr.versions.diff_contracts import MAX_SEARCH

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DATA_ROWS = 10_000
MAX_COLUMNS = 250
MAX_ZIP_ENTRIES = 1_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_REPORTED_ROWS = 100
PREVIEW_ROWS = 50
IMPORT_TTL_SECONDS = 15 * 60
MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_WORKSHEET_SOURCE_ROWS = 100_001
PROFILE_SCAN_ROWS = 100
PROFILE_SAMPLE_VALUES = 25
DISPLAY_SAMPLE_VALUES = 3
DISPLAY_SAMPLE_CHARS = 80
CANCEL_WAIT_SECONDS = 5.0

UNREADABLE_XLSX_MESSAGE = (
    "We couldn't read this modern Excel workbook. Open it in Excel or LibreOffice, save a new "
    ".xlsx copy, and upload that copy. Password-protected, damaged, or unsupported workbooks "
    "cannot be imported."
)


class ImportWorkflowMixin:
    def preview(self, actor_id, session_id, dataset_key, token, mapping, format_components=None):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self._bound_state(token, actor_id, session_id, dataset.id)
        if state.imported_count is not None or state.busy:
            raise ImportNotFoundError("Import not found")
        source = parse_source(state.filename, state.content, state.worksheet)
        if not source.headers:
            raise ImportNotFoundError("Import not found")
        mapping = tuple(mapping)
        retained_formats = {
            key: choice
            for key, choice in (state.source_formats or {}).items()
            if key in mapping
            and state.mapping
            and key in state.mapping
            and mapping.index(key) == state.mapping.index(key)
        }
        assistance = self._assistance(
            dataset.id,
            source,
            mapping,
            mode=state.mode,
            include_formats=True,
            choices=retained_formats,
        )
        if state.mode == "mapping_update":
            mapping = tuple(
                assistance.mapping[index] if index < len(assistance.mapping) else target
                for index, target in enumerate(mapping)
            )
        mapping_changed = mapping != state.mapping
        if format_components is not None:
            state, source, assistance, issues = self._submitted_source_format_assistance(
                state, source, assistance, mapping, format_components
            )
            if issues:
                raise SourceFormatComponentsError(issues)
        if mapping_changed:
            state = self.store.update(
                state,
                mapping=mapping,
                source_formats=retained_formats,
                mapping_ready=False,
                validation_valid=False,
                schema_fingerprint=None,
                step=3,
            )
        choices = {
            column["key"]: column["choice"]
            for column in assistance.format_columns
            if column is not None
        }
        if choices != state.source_formats:
            state = self.store.update(
                state,
                source_formats=choices,
                validation_valid=False,
                mapping_ready=False,
                schema_fingerprint=None,
                step=3,
            )
        preview = self._validate(
            dataset.id,
            state.filename,
            source,
            mapping,
            choices,
            mode=state.mode,
            actor_id=actor_id,
            concept_id=state.concept_id,
        )
        self.store.update(
            state,
            mapping=preview.mapping,
            schema_fingerprint=preview.schema_fingerprint,
            difference_digest=preview.difference_digest,
            confirmation=secrets.token_urlsafe(32),
            mapping_ready=not preview.non_row_errors,
            validation_valid=preview.valid,
            step=3 if preview.non_row_errors else 4,
        )
        return preview

    def saved_preview(
        self, actor_id, session_id, dataset_key, token, *, page=1, statuses=(), search=""
    ):
        if (
            type(page) is not int
            or page < 1
            or set(statuses) - {"Added", "Changed", "Deleted"}
            or not isinstance(search, str)
            or len(search) > MAX_SEARCH
        ):
            raise ImportValidationError("Choose valid difference filters")
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self._bound_state(token, actor_id, session_id, dataset.id)
        if not state.mapping_ready or state.imported_count is not None or state.busy:
            raise ImportNotFoundError("Import not found")
        source = parse_source(state.filename, state.content, state.worksheet)
        preview = self._validate(
            dataset.id,
            state.filename,
            source,
            state.mapping,
            state.source_formats,
            mode=state.mode,
            actor_id=actor_id,
            concept_id=state.concept_id,
            page=page,
            statuses=statuses,
            search=search,
        )
        self.store.update(
            state,
            validation_valid=preview.valid,
            mapping_ready=not preview.non_row_errors,
            schema_fingerprint=preview.schema_fingerprint,
            difference_digest=preview.difference_digest,
            confirmation=(
                state.confirmation
                if state.schema_fingerprint == preview.schema_fingerprint
                and state.difference_digest == preview.difference_digest
                else secrets.token_urlsafe(32)
            ),
        )
        return preview

    def _confirmed_import(self, uow, actor_id, validated_key, state, confirmation, acknowledge):
        dataset = uow.datasets.find_by_key(validated_key, for_update=True)
        self.records._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
        if dataset.id != state.dataset_id:
            raise ImportNotFoundError("Import not found")
        concept = uow.concepts.get(dataset.id)
        if concept is None or concept.id != state.concept_id:
            raise ImportNotFoundError("Import not found")
        if state.mode == "new_version":
            self._require_eligible(concept, uow=uow)
        source = parse_source(state.filename, state.content, state.worksheet)
        preview, validated = self._validate_in_uow(
            uow,
            dataset.id,
            state.filename,
            source,
            state.mapping,
            state.source_formats,
            mode=state.mode,
        )
        self._require_current_preview(preview, state, confirmation, acknowledge)
        imported_states, fields = self._persist_validated(uow, dataset, state, validated)
        self._record_confirmed_import(
            uow, actor_id, dataset, state, source, preview, validated, imported_states, fields
        )
        return preview, validated

    @staticmethod
    def _require_current_preview(preview, state, confirmation, acknowledge_deletions):
        if preview.schema_fingerprint != state.schema_fingerprint:
            if preview.schema_fingerprint[1:3] == state.schema_fingerprint[1:3]:
                raise ImportValidationError(
                    "The import is no longer valid; the Dataset changed. Validate again."
                )
            raise ImportValidationError("The Dataset schema changed; validate the import again")
        if not preview.valid:
            raise ImportValidationError("The import is no longer valid; validate it again")
        if state.mode in {"new_version", "mapping_update"}:
            if (
                not confirmation
                or confirmation != state.confirmation
                or preview.difference_digest != state.difference_digest
            ):
                raise ImportValidationError("Review the current difference and confirm again.")
            if preview.counts["Deleted"] and acknowledge_deletions is not True:
                raise ImportValidationError(
                    "Confirm the exact number of Records marked as deleted."
                )
        if state.mode == "mapping_update" and not preview.counts["Changed"]:
            raise ImportValidationError("No changes. The file matches the draft.")

    def _persist_validated(self, uow, dataset, state, validated):
        try:
            if state.mode == "mapping_update":
                imported_states, fields = self._persist_mapping_update(uow, dataset, validated), ()
            elif state.mode == "new_version":
                imported_states, fields = self._persist_new_version(uow, dataset, state, validated)
            else:
                imported_states, fields = {}, ()
                self._persist_created_records(uow, dataset, validated)
            uow.flush()
            return imported_states, fields
        except PersistenceConflictError as error:
            DatasetRecordService._raise_record_conflict(error)

    def _persist_mapping_update(self, uow, dataset, validated):
        changes = [
            (record, values, encoded)
            for status, record, values, encoded in self._differences(
                uow, dataset.id, validated, updates_only=True
            )
            if status == "Changed"
        ]
        for record, _values, _encoded in changes:
            uow.dataset_records.replace_values(record, ())
        for record, values, encoded in changes:
            if not uow.dataset_records.update_if_revision(
                dataset.id, record.id, record.revision, encoded
            ):
                raise ImportValidationError("Record changed. Review again.")
            uow.dataset_records.add_values(record, values)
        return {}

    def _persist_new_version(self, uow, dataset, state, validated):
        fields = uow.dataset_fields.list_for_dataset(dataset.id)
        mapped_field_ids = tuple(field.id for field in fields if field.key in state.mapping)
        changed, imported_states = {}, {}
        existing_ids = uow.dataset_records.existing_identifier_keys(
            dataset.id, tuple(encoded for _values, encoded in validated)
        )
        for status, record, _values, encoded in self._differences(
            uow, dataset.id, validated, mapped_keys=state.mapping
        ):
            if status == "Deleted":
                imported_states[record.id] = "Deleted"
                if not uow.dataset_records.delete_if_revision(
                    dataset.id, record.id, record.revision
                ):
                    raise ImportValidationError("Record changed. Review again.")
            elif status == "Changed":
                changed[encoded] = record
                imported_states[record.id] = "Changed"
        for record in changed.values():
            uow.dataset_records.replace_values(record, (), field_ids=mapped_field_ids)
        for values, identifier in validated:
            record = changed.get(identifier)
            if record:
                if not uow.dataset_records.update_if_revision(
                    dataset.id, record.id, record.revision, identifier
                ):
                    raise ImportValidationError("Record changed. Review again.")
                uow.dataset_records.add_values(record, values)
            elif identifier not in existing_ids:
                record = uow.dataset_records.create(
                    dataset_id=dataset.id, identifier_key=identifier
                )
                uow.flush()
                imported_states[record.id] = "Added"
                uow.dataset_records.add_values(record, values)
        return imported_states, fields

    @staticmethod
    def _persist_created_records(uow, dataset, validated):
        for values, identifier in validated:
            record = uow.dataset_records.create(dataset_id=dataset.id, identifier_key=identifier)
            uow.flush()
            uow.dataset_records.add_values(record, values)

    def _record_confirmed_import(
        self, uow, actor_id, dataset, state, source, preview, validated, imported_states, fields
    ):
        if not validated:
            return
        uow.dataset_fields.remember_source_formats(dataset.id, state.source_formats or {})
        result = (
            {k: preview.counts[k] for k in ("Added", "Changed", "Unchanged", "Deleted", "Error")}
            if state.mode == "new_version"
            else None
        )
        if result is not None:
            result["records"] = imported_states
        uow.concepts.record_mutation(dataset.id, import_result=result)
        advance_concept(uow, dataset)
        dataset.content_updated_at = uow.now()
        self._record_import_audit(
            uow, actor_id, dataset, state, source, preview, validated, result, fields
        )

    @staticmethod
    def _record_import_audit(
        uow, actor_id, dataset, state, source, preview, validated, result, fields
    ):
        if state.mode == "new_version":
            action = "dataset.new_version_imported"
            details = {
                "mode": "new_version",
                "format": source.kind,
                "added": result["Added"],
                "changed": result["Changed"],
                "unchanged": result["Unchanged"],
                "deleted": result["Deleted"],
                "total_processed": len(validated) + result["Deleted"],
                "unmapped_fields": [
                    field.key for field in fields if field.key not in state.mapping
                ],
            }
        elif state.mode == "mapping_update":
            action = "dataset.mapping_targets_updated"
            details = {
                "format": source.kind,
                "changed": preview.counts["Changed"],
                "unchanged": preview.counts["Unchanged"],
            }
        else:
            action = "dataset.records_imported"
            details = {
                "format": source.kind,
                "record_count": len(validated),
                "rejected_row_count": preview.invalid_rows,
            }
        record_audit(
            uow,
            actor_user_id=actor_id,
            object_type="dataset",
            object_id=dataset.id,
            action=action,
            details=details,
        )

    def confirm(
        self,
        actor_id,
        session_id,
        dataset_key,
        token,
        *,
        require_confirmation=False,
        confirmation=None,
        acknowledge_deletions=False,
    ) -> int:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow() as read_uow:  # type: ignore[attr-defined]
            target = read_uow.datasets.find_by_key(validated_key)
        if target is None:
            raise ImportNotFoundError("Dataset not found")
        self.records._require_access(  # type: ignore[attr-defined]
            actor_id, target, "can_maintain_dataset"
        )
        state = self._bound_state(  # type: ignore[attr-defined]
            token, actor_id, session_id, target.id
        )
        if (
            state.busy
            or state.imported_count is not None
            or (require_confirmation and (state.step != 4 or not state.validation_valid))
        ):
            raise ImportNotFoundError("Import not found")
        if not state.mapping_ready:
            raise ImportNotFoundError("Import not found")
        claimed = self.store.claim(state)  # type: ignore[attr-defined]
        try:
            with self.uow(  # type: ignore[attr-defined]
                write=True, serialized=True
            ) as uow:
                preview, validated = self._confirmed_import(
                    uow, actor_id, validated_key, state, confirmation, acknowledge_deletions
                )

        except (ValidationError, RecordConflictError):
            self.store.finish(  # type: ignore[attr-defined]
                claimed, validation_valid=False, step=4
            )
            raise
        except Exception:
            self.store.finish(claimed)  # type: ignore[attr-defined]
            raise
        updated_count = (
            preview.counts["Changed"] if state.mode == "mapping_update" else len(validated)
        )
        self.store.finish(  # type: ignore[attr-defined]
            claimed,
            content=b"",
            mapping=None,
            source_formats=None,
            schema_fingerprint=None,
            imported_count=updated_count,
            step=4 if state.mode == "mapping_update" else 5,
        )
        return updated_count

    def cancel(self, actor_id, session_id, dataset_key, token):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        self.store.cancel(token, actor_id, session_id, dataset.id)

    def revoke_session(self, session_id: str) -> None:
        self.store.revoke_session(session_id)  # type: ignore[attr-defined]
