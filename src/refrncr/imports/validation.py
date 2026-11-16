from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import replace

from refrncr.datasets import RecordValidationError
from refrncr.datasets.datatypes import get_datatype
from refrncr.datasets.record_identity import RecordIdentifierError, encode_record_identifier
from refrncr.datasets.records import MAX_RECORD_REVISION, DatasetRecordService
from refrncr.errors import ValidationError
from refrncr.imports.contracts import (
    ImportIssue,
    ImportPreview,
    mapping_issue_message,
    mapping_issues,
)
from refrncr.imports.errors import ImportNotFoundError, ImportValidationError
from refrncr.imports.profiling import build_mapping_assistance
from refrncr.imports.source_formats import (
    SourceFormatError,
    canonical_source,
)
from refrncr.presentation import record_label

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


class ImportValidationMixin:
    @staticmethod
    def _submitted_row(row, source, mapping, field_by_key, source_formats, choice_errors, mode):
        submitted, row_errors = {}, []
        for index, target in enumerate(mapping):
            if target is None or index in row.formulas:
                continue
            try:
                submitted[target] = canonical_source(
                    field_by_key[target].data_type,
                    row.source_value(index),
                    **source_formats[target],
                )
            except SourceFormatError as exc:
                if exc.requires_choice:
                    choice_errors[target] = ImportIssue(
                        None,
                        source.headers[index],
                        field_by_key[target].name,
                        "Choose an unambiguous source format for this field.",
                        "source_format_ambiguous",
                        index,
                        target,
                        mode,
                    )
                row_errors.append(
                    ImportIssue(
                        row.number, source.headers[index], field_by_key[target].name, str(exc)
                    )
                )
        for index in row.formulas:
            if mapping[index] is not None:
                row_errors.append(
                    ImportIssue(
                        row.number,
                        source.headers[index],
                        field_by_key[mapping[index]].name,
                        "Formula cells cannot be imported. Replace the formula with its value "
                        "or choose Do not import for this source column.",
                    )
                )
        return submitted, row_errors

    @staticmethod
    def _mapping_update_values(submitted, fields, identifier, existing_rows, field_by_key):
        id_fields = [field for field in fields if field.key in identifier.field_keys]
        _, encoded = DatasetRecordService._validated_values(
            id_fields,
            identifier,
            {key: submitted[key] for key in identifier.field_keys if key in submitted},
        )
        record = existing_rows.get(encoded)
        if record is None:
            raise ImportValidationError(
                "Source identifier does not exist in the linked source version"
            )
        retained = {
            value.field_key: get_datatype(value.data_type).text(value.value)
            for value in record.values
        }
        retained.update(
            {
                key: value
                for key, value in submitted.items()
                if field_by_key[key].mapping_role == "mapping_target"
            }
        )
        return retained

    def _validated_row_values(
        self,
        row,
        submitted,
        row_errors,
        mode,
        fields,
        identifier,
        existing_rows,
        field_by_key,
        mapping,
        source,
    ):
        try:
            if mode == "mapping_update":
                submitted = self._mapping_update_values(
                    submitted, fields, identifier, existing_rows, field_by_key
                )
            return DatasetRecordService._validated_values(fields, identifier, submitted)
        except RecordValidationError as error:
            for key, message in error.errors.items():
                if any(item.field == field_by_key[key].name for item in row_errors):
                    continue
                index = mapping.index(key) if key in mapping else None
                row_errors.append(
                    ImportIssue(
                        row.number,
                        source.headers[index] if index is not None else None,
                        field_by_key[key].name,
                        message,
                    )
                )
        except ValidationError as error:
            row_errors.append(ImportIssue(row.number, None, None, str(error)))
        return (), b""

    @staticmethod
    def _track_row_uniqueness(
        row, values, encoded, row_errors, seen_ids, seen_unique, source, mapping, field_by_key
    ):
        if row_errors:
            return False
        if encoded in seen_ids:
            row_errors.append(
                ImportIssue(row.number, None, None, "Record identifier is duplicated in this file")
            )
        for value in values:
            marker = (value.field_id, value.canonical_key)
            if value.is_unique and marker in seen_unique:
                row_errors.append(
                    ImportIssue(
                        row.number,
                        source.headers[mapping.index(value.field_key)]
                        if value.field_key in mapping
                        else None,
                        field_by_key[value.field_key].name,
                        "Unique value is duplicated in this file",
                    )
                )
        if row_errors:
            return False
        seen_ids.add(encoded)
        seen_unique.update(
            (value.field_id, value.canonical_key) for value in values if value.is_unique
        )
        return True

    def _difference_preview(
        self,
        preview,
        uow,
        dataset_id,
        accepted,
        mode,
        mapping,
        identifier,
        fields,
        statuses,
        search,
        page,
    ):
        counts = dict.fromkeys(("Added", "Changed", "Unchanged", "Deleted", "Error"), 0)
        counts["Error"] = preview.invalid_rows + len(preview.non_row_errors)
        digest = hashlib.sha256()
        differences, matched = [], 0
        term = unicodedata.normalize("NFC", search).casefold()
        for status, record, values, encoded in self._differences(
            uow,
            dataset_id,
            accepted,
            updates_only=mode == "mapping_update",
            mapped_keys=mapping if mode == "new_version" else None,
        ):
            counts[status] += 1
            self._update_difference_digest(digest, status, record, values, encoded)
            if status == "Unchanged":
                continue
            shown = values if status != "Deleted" else record.values
            by_key = {value.field_key: value for value in shown}
            label = record_label(
                get_datatype(by_key[key].data_type).text(by_key[key].value)
                for key in identifier.field_keys
            )
            if (statuses and status not in statuses) or term not in unicodedata.normalize(
                "NFC", label
            ).casefold():
                continue
            matched += 1
            if (page - 1) * 50 <= matched - 1 < page * 50:
                differences.append(self._difference_row(status, record, values, fields, label))
        counts["matching"] = matched
        return replace(
            preview,
            counts=counts,
            differences=tuple(differences),
            difference_digest=digest.hexdigest(),
        )

    @staticmethod
    def _update_difference_digest(digest, status, record, values, encoded):
        for part in (
            status.encode(),
            (record.id if record else "").encode(),
            encoded,
            str(record.revision if record else 0).encode(),
        ):
            digest.update(len(part).to_bytes(8, "big") + part)
        for collection in (record.values if record else (), values):
            digest.update(len(collection).to_bytes(8, "big"))
            for value in sorted(collection, key=lambda item: item.field_id):
                for part in (value.field_id.encode(), value.canonical_key):
                    digest.update(len(part).to_bytes(8, "big") + part)

    @staticmethod
    def _difference_row(status, record, values, fields, label):
        before = {value.field_id: value for value in record.values} if record else {}
        after = {value.field_id: value for value in values}
        changes = []
        for field in fields:
            old, new = before.get(field.id), after.get(field.id)
            if status == "Changed" and (old.canonical_key if old else None) == (
                new.canonical_key if new else None
            ):
                continue
            changes.append(
                (
                    field.name,
                    get_datatype(old.data_type).text(old.value) if old else None,
                    get_datatype(new.data_type).text(new.value) if new else None,
                )
            )
        current_values = (
            tuple(
                (
                    field.name,
                    get_datatype(before[field.id].data_type).text(before[field.id].value)
                    if field.id in before
                    else None,
                )
                for field in fields
            )
            if status == "Changed"
            else ()
        )
        return {
            "status": status,
            "identifier": label,
            "changes": tuple(changes),
            "current_values": current_values,
        }

    def _assistance(
        self,
        dataset_id,
        source,
        selected_mapping=None,
        *,
        mode="create",
        include_formats=False,
        choices=None,
    ):
        with self.uow() as uow:
            fields = uow.dataset_fields.list_for_dataset(dataset_id)
            identifier = uow.dataset_business_keys.get(dataset_id)
            if mode == "mapping_update":
                context_keys = {
                    field.key for field in fields if field.mapping_role == "source_context"
                }
                if selected_mapping is not None:
                    selected_mapping = tuple(
                        None if key in context_keys else key for key in selected_mapping
                    )
                fields = [field for field in fields if field.mapping_role != "source_context"]
            remembered = uow.dataset_fields.source_formats(dataset_id) if include_formats else {}
        assistance = build_mapping_assistance(
            source, fields, identifier, selected_mapping, mode=mode
        )
        if include_formats:
            format_columns = self._format_columns(
                source,
                fields,
                assistance.mapping,
                choices or {},
                remembered,
            )
            assistance = replace(
                assistance,
                format_columns=tuple(format_columns),
                remembered_formats=remembered,
            )
        return assistance

    def _validate(
        self,
        dataset_id,
        filename,
        source,
        mapping,
        source_formats=None,
        *,
        actor_id,
        concept_id,
        mode="create",
        page=1,
        statuses=(),
        search="",
    ):
        with self.uow() as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.get(dataset_id)
            self.records._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            concept = uow.concepts.get(dataset_id)
            if concept is None or concept.id != concept_id:
                raise ImportNotFoundError("Import not found")
            preview, _ = self._validate_in_uow(
                uow,
                dataset_id,
                filename,
                source,
                mapping,
                source_formats,
                mode=mode,
                page=page,
                statuses=statuses,
                search=search,
            )
            return preview

    def _validate_in_uow(
        self,
        uow,
        dataset_id,
        filename,
        source,
        mapping,
        source_formats=None,
        *,
        mode="create",
        page=1,
        statuses=(),
        search="",
    ):
        concept = uow.concepts.get(dataset_id)
        if mode == "new_version":
            self._require_eligible(concept, uow=uow)
        fields = uow.dataset_fields.list_for_dataset(dataset_id)
        identifier = uow.dataset_business_keys.get(dataset_id)
        field_by_key = {field.key: field for field in fields}
        mapping = tuple(mapping)
        if mode == "mapping_update":
            # A source context may be present in the file, but is never imported.
            mapping = tuple(
                None
                if key in field_by_key and field_by_key[key].mapping_role == "source_context"
                else key
                for key in mapping
            )
        import_fields = [
            field
            for field in fields
            if mode != "mapping_update" or field.mapping_role != "source_context"
        ]
        existing_rows = {}
        if mode == "mapping_update":
            stored_keys = uow.dataset_records.identifier_keys(dataset_id)
            existing_rows = {
                stored_keys[record.id]: record
                for record in uow.dataset_records.iterate_all(dataset_id)
            }
        coded_issues = list(
            mapping_issues(mapping, import_fields, identifier, mode, len(source.headers))
        )
        coded_issues.extend(
            self._source_format_issues(mapping, source, field_by_key, source_formats)
        )
        mapping_errors = [
            ImportIssue(
                None,
                source.headers[issue.column_index]
                if issue.column_index is not None and issue.column_index < len(source.headers)
                else None,
                field_by_key[issue.field_key].name if issue.field_key in field_by_key else None,
                mapping_issue_message(issue) or issue.message,
                issue.code,
                issue.column_index,
                issue.field_key,
                issue.mode or mode,
            )
            for issue in coded_issues
        ]
        fingerprint = (
            uow.datasets.get(dataset_id).revision,
            tuple(
                (
                    field.id,
                    field.key,
                    field.name,
                    field.data_type,
                    field.is_required,
                    field.is_unique,
                )
                for field in fields
            ),
            identifier.field_keys,
            (concept.id, concept.start_revision, concept.updated_at.isoformat()),
        )
        if mapping_errors:
            return ImportPreview(
                filename,
                source,
                mapping,
                0,
                0,
                tuple(mapping_errors),
                len(mapping_errors),
                False,
                (),
                fingerprint,
            ), []
        choice_errors = {}
        seen_ids, seen_unique, validated, errors, invalid_rows = set(), set(), [], [], set()
        for row in source.rows:
            submitted, row_errors = self._submitted_row(
                row, source, mapping, field_by_key, source_formats, choice_errors, mode
            )
            values, encoded = self._validated_row_values(
                row,
                submitted,
                row_errors,
                mode,
                fields,
                identifier,
                existing_rows,
                field_by_key,
                mapping,
                source,
            )
            if self._track_row_uniqueness(
                row,
                values,
                encoded,
                row_errors,
                seen_ids,
                seen_unique,
                source,
                mapping,
                field_by_key,
            ):
                validated.append((row, values, encoded))
            else:
                invalid_rows.add(row.number)
                errors.extend(row_errors)
        existing_identifiers = (
            set()
            if mode in {"new_version", "mapping_update"}
            else uow.dataset_records.existing_identifier_keys(
                dataset_id, tuple(encoded for _row, _values, encoded in validated)
            )
        )
        unique_candidates = tuple(
            (value.field_id, value.canonical_key)
            for _row, values, _encoded in validated
            for value in values
            if value.is_unique
        )
        updated_ids = {encoded for _row, _values, encoded in validated}
        existing_unique = (
            {
                (value.field_id, value.canonical_key)
                for encoded, record in existing_rows.items()
                if encoded not in updated_ids
                for value in record.values
                if value.is_unique
            }
            if mode == "mapping_update"
            else set()
            if mode == "new_version"
            else uow.dataset_records.existing_unique_value_keys(dataset_id, unique_candidates)
        )
        accepted = []
        for row, values, encoded in validated:
            database_errors = []
            if encoded in existing_identifiers:
                database_errors.append(
                    ImportIssue(
                        row.number,
                        None,
                        None,
                        "Record identifier already exists in this Dataset",
                    )
                )
            for value in values:
                marker = (value.field_id, value.canonical_key)
                if value.is_unique and marker in existing_unique:
                    database_errors.append(
                        ImportIssue(
                            row.number,
                            source.headers[mapping.index(value.field_key)]
                            if value.field_key in mapping
                            else None,
                            field_by_key[value.field_key].name,
                            "Unique value already exists in this Dataset",
                        )
                    )
            if database_errors:
                invalid_rows.add(row.number)
                errors.extend(database_errors)
            else:
                accepted.append((values, encoded))
        errors.extend(choice_errors.values())
        errors.sort(
            key=lambda item: (
                item.row is not None,
                item.row or 0,
                item.header or "",
                item.field or "",
                item.message,
            )
        )
        reported_rows = set(sorted(invalid_rows)[:MAX_REPORTED_ROWS])
        reported_errors = tuple(
            error for error in errors if error.row is None or error.row in reported_rows
        )
        sample = tuple(
            tuple(
                next(
                    (
                        get_datatype(value.data_type).text(value.value)
                        for value in values
                        if value.field_key == field.key
                    ),
                    "",
                )
                for field in fields
                if field.key in mapping
            )
            for values, _encoded in accepted[:PREVIEW_ROWS]
        )
        preview = ImportPreview(
            filename,
            source,
            mapping,
            len(source.rows) - len(invalid_rows),
            len(invalid_rows),
            reported_errors,
            len(errors),
            len(invalid_rows) > MAX_REPORTED_ROWS,
            sample,
            fingerprint,
        )
        if mode not in {"new_version", "mapping_update"}:
            preview = self._add_create_preview(
                preview, accepted, fields, mapping, identifier, search, page
            )
        if mode in {"new_version", "mapping_update"}:
            preview = self._difference_preview(
                preview,
                uow,
                dataset_id,
                accepted,
                mode,
                mapping,
                identifier,
                fields,
                statuses,
                search,
                page,
            )
        return preview, accepted

    @staticmethod
    def _source_format_issues(mapping, source, field_by_key, source_formats):
        source_formats = source_formats or {}
        return [
            ImportIssue(
                None,
                source.headers[index] if index < len(source.headers) else None,
                field_by_key[key].name,
                "Choose an unambiguous source format for this field.",
                "source_format_ambiguous",
                index,
                key,
            )
            for index, key in enumerate(mapping)
            if key in field_by_key and not source_formats.get(key, {}).get("profile_id")
        ]

    @staticmethod
    def _add_create_preview(preview, accepted, fields, mapping, identifier, search, page):
        term = unicodedata.normalize("NFC", search).casefold()
        filtered = []
        for values, _encoded in accepted:
            by_key = {value.field_key: value for value in values}
            label = record_label(
                get_datatype(by_key[key].data_type).text(by_key[key].value)
                for key in identifier.field_keys
            )
            if term in unicodedata.normalize("NFC", label).casefold():
                filtered.append(values)
        sample = tuple(
            tuple(
                next(
                    (
                        get_datatype(value.data_type).text(value.value)
                        for value in values
                        if value.field_key == field.key
                    ),
                    "",
                )
                for field in fields
                if field.key in mapping
            )
            for values in filtered[(page - 1) * 50 : page * 50]
        )
        counts = dict.fromkeys(("Added", "Changed", "Unchanged", "Deleted", "Error"), 0)
        counts["Added"] = len(accepted)
        counts["Error"] = preview.invalid_rows + len(preview.non_row_errors)
        counts["matching"] = len(filtered)
        return replace(preview, sample=sample, counts=counts)

    def _differences(self, uow, dataset_id, accepted, *, updates_only=False, mapped_keys=None):
        proposed = {encoded: values for values, encoded in accepted}
        identifier = uow.dataset_business_keys.get(dataset_id)
        stored_keys = uow.dataset_records.identifier_keys(dataset_id)
        seen = set()
        for record in uow.dataset_records.iterate_all(dataset_id):
            by_key = {v.field_key: v for v in record.values}
            if any(key not in by_key for key in identifier.field_keys):
                raise ImportValidationError(
                    "Stored Record identifiers are inconsistent. Import unavailable."
                )
            try:
                encoded = encode_record_identifier(
                    {key: by_key[key].canonical_key for key in identifier.field_keys},
                    expected_keys=identifier.field_keys,
                )
            except RecordIdentifierError:
                raise ImportValidationError(
                    "Stored Record identifiers are inconsistent. Import unavailable."
                ) from None
            if encoded in seen or stored_keys.get(record.id) != encoded:
                raise ImportValidationError(
                    "Stored Record identifiers are inconsistent. Import unavailable."
                )
            seen.add(encoded)
            values = proposed.get(encoded)
            if updates_only and values is None:
                continue
            if values is not None and mapped_keys is not None:
                # Compare and preview the complete result, retaining draft values only
                # for omitted fields. A mapped blank deliberately has no new value.
                values = values + tuple(
                    value for value in record.values if value.field_key not in mapped_keys
                )
            status = (
                "Deleted"
                if values is None
                else "Unchanged"
                if {v.field_id: v.canonical_key for v in record.values}
                == {v.field_id: v.canonical_key for v in values}
                else "Changed"
            )
            if status != "Unchanged" and record.revision >= MAX_RECORD_REVISION:
                raise ImportValidationError(
                    "A Record reached its revision limit. Import unavailable."
                )
            yield status, record, values or (), encoded
        for encoded, values in proposed.items():
            if encoded not in seen:
                yield "Added", None, values, encoded
