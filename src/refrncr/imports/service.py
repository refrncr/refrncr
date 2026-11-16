from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import date

from refrncr.datasets.datatypes import get_datatype
from refrncr.datasets.records import DatasetRecordService
from refrncr.domain import require_concept
from refrncr.imports.contracts import (
    ImportNotFoundError,
    ImportValidationError,
    validate_source_format_components,
)
from refrncr.imports.parsing import parse_source
from refrncr.imports.profiling import (
    format_verdict,
    native_date_verdict,
    safe_sample,
    temporal_example,
)
from refrncr.imports.source_formats import (
    MAX_SOURCE_TIMEZONE_LENGTH,
    SourceFormatError,
    canonical_source,
    formats_for,
    propose_format,
    source_format_label,
    temporal_controls,
)
from refrncr.imports.store import TransientImportStore
from refrncr.imports.upload_names import safe_upload_filename
from refrncr.imports.validation import ImportValidationMixin
from refrncr.imports.workflows import ImportWorkflowMixin

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


def _validated_source_format_choice(raw_choice, field):
    if (
        not isinstance(raw_choice, Mapping)
        or set(raw_choice) - {"profile_id", "timezone", "fold"}
        or any(
            not isinstance(value, str) or len(value) > 100
            for name, value in raw_choice.items()
            if name != "timezone"
        )
        or not isinstance(raw_choice.get("timezone", ""), str)
        or len(raw_choice.get("timezone", "")) > MAX_SOURCE_TIMEZONE_LENGTH
    ):
        raise ImportValidationError("Submit a valid bounded source-format choice.")
    choice = dict(raw_choice)
    if field.data_type != "timestamp" or choice.get("profile_id") == "v1-offset":
        if choice.get("timezone") or choice.get("fold"):
            raise ImportValidationError(
                "Timezone and repeated-time choices require a local Timestamp format."
            )
        choice.pop("timezone", None)
        choice.pop("fold", None)
    return choice


class DatasetRecordImportService(ImportWorkflowMixin, ImportValidationMixin):
    def __init__(self, records: DatasetRecordService, store: TransientImportStore | None = None):
        self.records = records
        self.uow = records.uow
        self.authorization = records.authorization
        self.store = store or TransientImportStore()

    def _concept(self, dataset_id):
        with self.uow() as uow:
            return require_concept(uow, dataset_id)

    @staticmethod
    def _eligibility(concept, has_non_cancelled_version):
        if concept.records_mutated is None:
            return (
                "Import unavailable",
                "This Draft was created before new-version imports were introduced, "
                "so refrncr cannot reliably verify that its Records are unchanged. "
                "Publish or discard this Draft, then create a new Draft "
                "to import a new version.",
            )
        if not has_non_cancelled_version:
            return None
        if concept.import_result is not None:
            return (
                "Import already completed",
                "A new version has already been imported into this Draft. "
                "Review and publish or discard this Draft before starting another import.",
            )
        if concept.records_mutated:
            return (
                "Import unavailable",
                "Records in this Draft have already been changed. For safety, an import can't "
                "be combined with other changes: an import doesn't add Records; the file "
                "describes the complete set of Records you want. refrncr compares it with the "
                "current Records and marks each one as Added, Changed, Unchanged or Deleted. "
                "Publish or discard this Draft, then create a new Draft to import a new version.",
            )
        return None

    def eligibility(self, actor_id, dataset_key):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        with self.uow() as uow:
            concept = require_concept(uow, dataset.id)
            has_non_cancelled_version = uow.versions.latest_non_cancelled(dataset.id) is not None
        return (
            None
            if dataset.kind == "mapping"
            else self._eligibility(concept, has_non_cancelled_version)
        )

    def _require_eligible(self, concept, *, uow=None):
        if uow is None:
            with self.uow() as read_uow:
                has_non_cancelled_version = (
                    read_uow.versions.latest_non_cancelled(concept.dataset_id) is not None
                )
        else:
            has_non_cancelled_version = (
                uow.versions.latest_non_cancelled(concept.dataset_id) is not None
            )
        reason = self._eligibility(concept, has_non_cancelled_version)
        if reason:
            raise ImportValidationError(". ".join(reason))

    def _bound_state(self, token, actor_id, session_id, dataset_id):
        state = self.store.get(token, actor_id, session_id, dataset_id)
        return self._validate_bound_state(state, dataset_id)

    def _validate_bound_state(self, state, dataset_id):
        with self.uow() as uow:
            concept = uow.concepts.get(dataset_id)
            if concept is None or state.concept_id != concept.id:
                self.store.revoke_concept(state.concept_id)
                raise ImportNotFoundError("Import not found")
            if state.mode == "new_version" and state.imported_count is None:
                self._require_eligible(concept, uow=uow)
        return state

    @contextmanager
    def source_format_review(self, actor_id, session_id, dataset_key, token):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        with self.store._operation(token, actor_id, session_id, dataset.id) as state:
            state = self._validate_bound_state(state, dataset.id)
            if state.busy:
                raise ImportNotFoundError("Import not found")
            yield state

    def active(self, actor_id, session_id, dataset_key):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self.store.active(actor_id, session_id, dataset.id)
        if state and state.concept_id != self._concept(dataset.id).id:
            self.store.revoke_concept(state.concept_id)
            return None
        return state

    def state(self, actor_id, session_id, dataset_key, token):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self._bound_state(token, actor_id, session_id, dataset.id)
        if state.busy:
            raise ImportNotFoundError("Import not found")
        return state

    def begin(self, actor_id, session_id, dataset_key, filename, content, *, mode="create"):
        if mode not in {"create", "new_version", "mapping_update"}:
            raise ImportValidationError("Choose a valid import mode")
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        if dataset.kind == "mapping":
            mode = "mapping_update"
        elif mode == "mapping_update":
            raise ImportValidationError("Target updates require a mapping dataset")
        concept = self._concept(dataset.id)
        if mode == "new_version":
            self._require_eligible(concept)
        previous = self.active(actor_id, session_id, dataset_key)
        if previous and previous.mode != mode:
            raise ImportValidationError(
                "Cancel the active import before choosing another import mode"
            )
        safe_name = safe_upload_filename(filename)
        source = parse_source(safe_name, content)
        if source.kind == "xlsx":
            worksheet = None
            if previous and previous.kind == "xlsx" and previous.worksheet in source.worksheets:
                worksheet = previous.worksheet
            elif len(source.worksheets) == 1:
                worksheet = source.worksheets[0]
            if worksheet:
                source = parse_source(safe_name, content, worksheet)
        assistance = self._assistance(dataset.id, source, mode=mode) if source.headers else None
        mapping = assistance.mapping if assistance else None
        if previous and previous.mapping is not None and source.headers:
            old = parse_source(previous.filename, previous.content, previous.worksheet)
            if old.kind == source.kind and old.worksheet == source.worksheet:
                retained = dict(zip(old.headers, previous.mapping, strict=False))
                with self.uow() as uow:
                    allowed = {
                        field.key for field in uow.dataset_fields.list_for_dataset(dataset.id)
                    }
                retained = {
                    header: target
                    for header, target in retained.items()
                    if header in source.headers and (target is None or target in allowed)
                }
                used = {target for target in retained.values() if target}
                mapping = tuple(
                    retained[header]
                    if header in retained
                    else ""
                    if mapping[index] in used
                    else mapping[index]
                    for index, header in enumerate(source.headers)
                )
                assistance = self._assistance(dataset.id, source, mapping, mode=mode)
        state = self.store.create(
            actor_id,
            session_id,
            dataset.id,
            safe_name,
            content,
            source.kind,
            previous,
            concept_id=concept.id,
            mode=mode,
        )
        state = self.store.update(
            state, worksheet=source.worksheet, mapping=mapping, step=3 if source.headers else 2
        )
        return state, source, assistance

    def navigate(self, actor_id, session_id, dataset_key, token, step):
        state = self.state(actor_id, session_id, dataset_key, token)
        if state.imported_count is not None:
            if step != 4:
                raise ImportNotFoundError("Import not found")
            return state
        source = parse_source(state.filename, state.content, state.worksheet)
        reachable = {1}
        if source.kind == "xlsx" and len(source.worksheets) > 1:
            reachable.add(2)
        if source.headers:
            reachable.add(3)
        if state.mapping_ready:
            reachable.add(4)
        if step not in reachable:
            raise ImportNotFoundError("Import not found")
        return self.store.update(state, step=step)

    def select_worksheet(self, actor_id, session_id, dataset_key, token, worksheet):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self.state(actor_id, session_id, dataset_key, token)
        if state.imported_count is not None:
            raise ImportNotFoundError("Import not found")
        workbook = parse_source(state.filename, state.content)
        if workbook.kind != "xlsx" or len(workbook.worksheets) <= 1:
            raise ImportNotFoundError("Import not found")
        source = parse_source(state.filename, state.content, worksheet)
        if state.worksheet != worksheet:
            state = self.store.update(
                state,
                worksheet=worksheet,
                mapping=None,
                source_formats=None,
                mapping_ready=False,
                validation_valid=False,
                schema_fingerprint=None,
                step=3,
            )
        else:
            state = self.store.update(state, step=3)
        assistance = self._assistance(dataset.id, source, state.mapping, mode=state.mode)
        state = self.store.update(state, mapping=assistance.mapping, step=3)
        return state, source, assistance

    def mapping_assistance(
        self,
        actor_id,
        session_id,
        dataset_key,
        token,
        selected_mapping=None,
        *,
        include_formats=False,
    ):
        dataset = self.records.get_dataset_for_add(actor_id, dataset_key)
        state = self._bound_state(token, actor_id, session_id, dataset.id)
        if state.imported_count is not None or state.busy:
            raise ImportNotFoundError("Import not found")
        source = parse_source(state.filename, state.content, state.worksheet)
        if not source.headers:
            raise ImportNotFoundError("Import not found")
        mapping = state.mapping if selected_mapping is None else tuple(selected_mapping)
        return (
            state,
            source,
            self._assistance(
                dataset.id,
                source,
                mapping,
                mode=state.mode,
                include_formats=include_formats,
                choices=state.source_formats,
            ),
        )

    def format_assistance(
        self, actor_id, session_id, dataset_key, token, mapping=None, choices=None
    ):
        state, source, assistance = self.mapping_assistance(
            actor_id, session_id, dataset_key, token
        )
        mapping = tuple(mapping) if mapping is not None else assistance.mapping
        with self.uow() as uow:
            fields = uow.dataset_fields.list_for_dataset(state.dataset_id)
            remembered = uow.dataset_fields.source_formats(state.dataset_id)
        selected = choices if choices is not None else (state.source_formats or {})
        return self._format_columns(source, fields, mapping, selected, remembered)

    def submitted_source_formats(
        self, actor_id, session_id, dataset_key, token, mapping, components
    ):
        _state, _source, assistance = self.mapping_assistance(
            actor_id, session_id, dataset_key, token, mapping
        )
        return self._submitted_source_formats(assistance, mapping, components)

    @staticmethod
    def _submitted_source_formats(assistance, mapping, components):
        return validate_source_format_components(assistance.fields, mapping, components)

    def submitted_source_format_assistance(
        self, actor_id, session_id, dataset_key, token, mapping, components
    ):
        state, source, assistance = self.mapping_assistance(
            actor_id, session_id, dataset_key, token, mapping, include_formats=True
        )
        return self._submitted_source_format_assistance(
            state, source, assistance, mapping, components
        )

    def _submitted_source_format_assistance(self, state, source, assistance, mapping, components):
        choices, issues = validate_source_format_components(assistance.fields, mapping, components)
        if not issues:
            assistance = replace(
                assistance,
                format_columns=tuple(
                    self._format_columns(
                        source,
                        assistance.fields,
                        mapping,
                        choices,
                        assistance.remembered_formats or {},
                    )
                ),
            )
        return state, source, assistance, issues

    @staticmethod
    def _format_columns(source, fields, mapping, choices, remembered):
        by_key = {field.key: field for field in fields}
        columns = []
        for index, key in enumerate(mapping):
            field = by_key.get(key)
            if field is None or index >= len(source.headers):
                columns.append(None)
                continue
            values = tuple(
                row.source_value(index) for row in source.rows if index not in row.formulas
            )
            choice = _validated_source_format_choice(choices.get(key, {}), field)
            error = None
            matches = []
            if not choice.get("profile_id"):
                choice, error = propose_format(
                    field.data_type, values, remembered.get(key), readings=matches
                )
            examples = []
            first_example_value = None
            seen_examples = set()
            failures = 0
            for row in source.rows:
                if index in row.formulas:
                    continue
                value = row.source_value(index)
                canonical = None
                if choice.get("profile_id"):
                    try:
                        canonical = canonical_source(field.data_type, value, **choice)
                    except SourceFormatError as exc:
                        failures += value is not None and value != ""
                        error = str(exc)
                if value == "" or value is None:
                    continue
                # Distinct values only: three identical examples say nothing.
                if len(examples) == DISPLAY_SAMPLE_VALUES:
                    continue
                display_value = temporal_example(value, field.data_type)
                example = {
                    "source": safe_sample(display_value),
                    "full_source": display_value[:4096],
                    "stored": safe_sample(canonical) if canonical is not None else "—",
                    "full_stored": canonical,
                }
                if str(value) in seen_examples:
                    continue
                seen_examples.add(str(value))
                if not examples:
                    first_example_value = value
                examples.append(example)
            if failures:
                error = f"{failures} non-empty values do not fit. {error}"
            profiles = formats_for(field.data_type)
            selected_profile = next((p for p in profiles if p.id == choice.get("profile_id")), None)
            label = selected_profile.label if selected_profile else "Choose source format"
            total = sum(value is not None and value != "" for value in values)
            if not choice.get("profile_id"):
                presentation_state = "undecided"
            elif failures:
                presentation_state = "failing"
            elif error:
                presentation_state = "undecided"
            elif any(example["source"] != example["stored"] for example in examples):
                presentation_state = "transformed"
            else:
                presentation_state = "clean"
            verdict = format_verdict(presentation_state, label, examples, total, failures)
            if (
                selected_profile
                and selected_profile.kind == "native"
                and field.data_type in {"date", "datetime", "timestamp"}
                and presentation_state in {"clean", "transformed"}
                and isinstance(first_example_value, date)
            ):
                verdict = native_date_verdict(
                    first_example_value, examples[0]["stored"], field.data_type, presentation_state
                )
            columns.append(
                {
                    "state": presentation_state,
                    "verdict": verdict,
                    "total_values": total,
                    "readings": [
                        {
                            "id": profile.id,
                            "label": source_format_label(profile),
                            "example_source": examples[0]["source"],
                            "example_stored": safe_sample(canonical),
                        }
                        for profile, canonical in matches
                    ],
                    "key": key,
                    "datatype": get_datatype(field.data_type).label,
                    "choice": choice,
                    "error": error,
                    "examples": examples,
                    "profiles": profiles,
                    "label": label,
                    "applicable": field.data_type != "string",
                    "timestamp": field.data_type == "timestamp",
                    "temporal": temporal_controls(
                        field.data_type, selected_profile.id if selected_profile else ""
                    ),
                }
            )
        return columns
