"""Authoritative synchronous Dataset publication and cancellation use cases."""

from dataclasses import replace
from datetime import date
from itertools import chain
from types import MappingProxyType
from unicodedata import normalize

from refrncr.audit import record_audit
from refrncr.datasets.dataset_diff import DatasetDiffService
from refrncr.datasets.datatypes import get_datatype
from refrncr.datasets.mapping_updates import mapping_states
from refrncr.datasets.record_search import RecordSearchError, matches, search_term
from refrncr.datasets.records import DatasetRecordService, RecordPage
from refrncr.domain import require_concept
from refrncr.hierarchy import (
    hierarchy_leaf_records,
    hierarchy_validation_errors,
)
from refrncr.hierarchy_contracts import hierarchy_node_paths
from refrncr.ports import (
    DatasetBusinessKey,
    PersistenceConflictError,
)
from refrncr.presentation import DISPLAY_STATUSES, record_label, status_label
from refrncr.versions.change_status import (
    RECORD_STATUS_FILTERS,
)
from refrncr.versions.diff_classification import classify_fields, summary_counts
from refrncr.versions.errors import (
    VersionConflictError,
    VersionNotFoundError,
    VersionValidationError,
)
from refrncr.versions.version_contracts import (
    MAX_RELEASE_NOTE,
    SummaryCounts,
    VersionRead,
    version_states,
)


class DatasetVersionLifecycleMixin:
    def read(
        self,
        actor_id,
        key,
        version_id=None,
        page=1,
        *,
        prefer_concept=False,
        search="",
        statuses=(),
        record_status="all",
        mapping_status="all",
    ):
        if page is not None and (type(page) is not int or page < 1):
            raise VersionValidationError("Page must be a positive integer")
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, state, maintain = self._selection(
                uow, actor_id, key, version_id, prefer_concept=prefer_concept
            )
            search = search_term(search)
            selected_changes = tuple(record_status.split(",")) if record_status != "all" else ()
            change_order = tuple(RECORD_STATUS_FILTERS)
            if (
                any(change not in change_order for change in selected_changes)
                or tuple(change for change in change_order if change in selected_changes)
                != selected_changes
            ):
                raise RecordSearchError("Choose Added, Changed or Deleted filters.")
            if (
                not isinstance(statuses, tuple)
                or any(status not in DISPLAY_STATUSES for status in statuses)
                or len(set(statuses)) != len(statuses)
                or (state is not None and (statuses or selected_changes))
            ):
                raise RecordSearchError("Choose valid statuses for this Dataset version.")
            if mapping_status not in {"all", "unmapped", "removed"} or (
                mapping_status != "all" and (state is not None or dataset.kind != "mapping")
            ):
                raise RecordSearchError("Choose a valid mapping draft filter.")
            context = self._read_context(uow, dataset, state)
            if state is None:
                fields = tuple(uow.dataset_fields.list_for_dataset(dataset.id))
                identifier = uow.dataset_business_keys.get(dataset.id)
                records = uow.dataset_records.iterate_all(dataset.id) if page is not None else ()
            else:
                fields = uow.versions.fields(state.version)
                identifier = DatasetBusinessKey(
                    dataset.id, tuple(sorted(field.key for field in fields if field.is_identifier))
                )
                records = uow.versions.iterate_records(state.version) if page is not None else ()
            hierarchy_nodes, hierarchy_errors = (), ()
            if dataset.kind == "hierarchy":
                hierarchy_nodes = (
                    uow.hierarchy.list_for_dataset(dataset.id)
                    if state is None
                    else uow.versions.hierarchy_nodes(state.version)
                )
                records = (
                    hierarchy_leaf_records(hierarchy_nodes, fields) if page is not None else ()
                )
                if state is None:
                    hierarchy_errors = hierarchy_validation_errors(
                        fields, identifier, hierarchy_nodes
                    )
            node_names = hierarchy_node_paths(hierarchy_nodes)
            concept = uow.concepts.get(dataset.id) if maintain else None
            baseline_fields, removed = (), ()
            deleted_fields = {}
            imported = (concept.import_result or {}).get("records", {}) if concept else {}
            if state is None and page is not None:
                pairs = tuple(uow.dataset_records.deleted_records(dataset.id))
                baseline = (
                    uow.versions.get(dataset.id, concept.base_version_id)
                    if concept.base_version_id
                    else None
                )
                baseline_ids = set(
                    uow.versions.record_ids(baseline) if baseline is not None else set()
                )
                removed = tuple(
                    record
                    for record, _fields in pairs
                    if dataset.kind == "mapping" or record.id in baseline_ids
                )
                deleted_fields = {record.id: field_set for record, field_set in pairs}
            deleted_ids = {record.id for record in removed}
            labels = {}

            def sort_key(record):
                keys = (
                    tuple(sorted(f.key for f in deleted_fields[record.id] if f.is_identifier))
                    if record.id in deleted_ids
                    else identifier.field_keys
                )
                values = {value.field_key: value for value in record.values}
                label = record_label(
                    get_datatype(values[key].data_type).text(values[key].value)
                    if key in values
                    else None
                    for key in keys
                )
                if dataset.kind == "hierarchy":
                    labels[record.id] = node_names[record.id] if not keys else label
                    if not keys:
                        return False, ((0, "string", node_names[record.id].casefold()),), record.id
                else:
                    labels[record.id] = label
                incomplete, components, _id = DatasetRecordService._record_sort_key(record, keys)
                # Old/new identifier datatypes can differ. Type tags preserve typed ordering
                # within each datatype and make their combined ordering total and stable.
                return (
                    incomplete,
                    tuple(
                        (component[0], values[key].data_type if key in values else "", component[1])
                        for key, component in zip(keys, components, strict=True)
                    ),
                    record.id,
                )

            ordered = sorted(chain(records, removed), key=sort_key)
            term = normalize("NFC", search).casefold()
            matching = [
                record
                for record in ordered
                if matches(record, fields, term)
                or (dataset.kind == "hierarchy" and term in node_names[record.id].casefold())
            ]
            projected = {}
            if state is None:
                for offset in range(0, len(matching), 500):
                    projected.update(
                        uow.version_diff.record_states(
                            dataset.id,
                            concept.base_version_id,
                            tuple(r.id for r in matching[offset : offset + 500]),
                        )
                    )
            raw_states = {
                record.id: (
                    "Deleted"
                    if record.id in deleted_ids
                    else imported.get(record.id, projected.get(record.id))
                )
                for record in matching
            }
            status_counts = tuple(
                sum(raw_states[record.id] == label for record in matching)
                for label in DISPLAY_STATUSES
            )
            mapping_record_states = {}
            if state is None and dataset.kind == "mapping":
                mapping_record_states = mapping_states(fields, ordered, removed)
            ordered = matching
            if mapping_status != "all":
                wanted = "Unmapped" if mapping_status == "unmapped" else "Removed from source"
                ordered = [r for r in ordered if mapping_record_states.get(r.id) == wanted]
            if statuses and imported:
                imported_ids = set(imported)
                ordered = [record for record in ordered if record.id in imported_ids]
            if selected_changes:
                selected_raw = {
                    status_label(RECORD_STATUS_FILTERS[change]) for change in selected_changes
                }
                ordered = [record for record in ordered if raw_states[record.id] in selected_raw]
            if statuses and state is None:
                ordered = [record for record in ordered if raw_states[record.id] in statuses]
            page_size = DatasetRecordService.PAGE_SIZE
            start = ((page or 1) - 1) * page_size
            record_page = RecordPage(
                tuple(ordered[start : start + page_size]),
                page or 1,
                page_size,
                len(ordered) if page is not None else context.record_count,
            )
            states = {
                record.id: raw_states[record.id]
                for record in record_page.records
                if raw_states.get(record.id) is not None
            }
            export = self.authorization.can_export_dataset(actor_id, dataset.id, uow=uow)
            return VersionRead(
                context,
                fields,
                identifier,
                record_page,
                state,
                maintain,
                export and (state is None or state.status in {"Current", "Expired"}),
                concept,
                MappingProxyType(
                    {
                        r.id: (
                            "Deleted"
                            if r.id in deleted_ids
                            else status_label(states[r.id])
                            if r.id in states
                            else "Published"
                        )
                        for r in record_page.records
                    }
                ),
                len(removed),
                baseline_fields,
                MappingProxyType({r.id: labels[r.id] for r in record_page.records}),
                search,
                MappingProxyType(deleted_fields),
                status_counts,
                hierarchy_nodes,
                hierarchy_errors,
                MappingProxyType(mapping_record_states),
            )

    def review(self, actor_id, dataset_id):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = self._target(uow, actor_id, dataset_id)
            content, summary, number = self._prepare(uow, dataset, self.clock.today())
            return dataset.revision, number, content, summary

    def _prepare(self, uow, dataset, today):
        concept = require_concept(uow, dataset.id)
        versions = uow.versions.list_for_dataset(dataset.id)
        baseline = next((v for v in versions if v.id == concept.base_version_id), None)
        if concept.base_version_id is not None and (
            baseline is None or baseline.cancelled_at is not None
        ):
            raise VersionConflictError(
                "The concept basis was cancelled. Publication is blocked; "
                "your changes are preserved."
            )
        if any(s.status == "Scheduled" for s in version_states(versions, today)):
            raise VersionConflictError("Cancel the Scheduled version or wait until it is Current")
        content = self._snapshot(uow, dataset)
        latest = next((v for v in versions if v.cancelled_at is None), None)
        if (latest.id if latest else None) != concept.base_version_id:
            raise VersionConflictError(
                "The concept basis is no longer the latest version. Your changes are preserved."
            )
        if latest is None:
            summary = SummaryCounts(
                fields_added=len(content.fields), records_added=len(content.records)
            )
            changed = True
        else:
            if not uow.version_diff.validate_snapshot(latest):
                raise VersionValidationError("Invalid published snapshot")
            fields = classify_fields(content.fields, uow.versions.fields(latest))
            summary = summary_counts(fields, uow.version_diff.counts(dataset.id, latest.id))
            changed = any(getattr(summary, key) for key in SummaryCounts.__dataclass_fields__)
            changed = changed or dataset.source_version_id != latest.source_version_id
            if dataset.kind == "hierarchy":
                changed = changed or bool(uow.version_diff.hierarchy_changes(dataset.id, latest.id))
        if not changed:
            raise VersionConflictError("Dataset content matches the latest published version")
        return content, summary, max((v.number for v in versions), default=0) + 1

    def publish(
        self,
        actor_id,
        dataset_id,
        effective_date,
        release_note,
        expected_revision,
        *,
        expected_review=None,
    ):
        try:
            with self.uow(write=True, serialized=True, translate_errors=True) as uow:
                dataset = self._target(uow, actor_id, dataset_id, write=True)
                if type(expected_revision) is not int or expected_revision != dataset.revision:
                    raise VersionConflictError("Dataset changed. Review the current content again.")
                if expected_review is not None:
                    # Optional REF-84 context, checked under this same publication lock.
                    # Ordinary REF-83 publication remains independent of detailed review.
                    diff = DatasetDiffService(self)
                    review_state = diff._load(uow, actor_id, dataset_id, expected_review)[2]
                now = self.clock.now()
                today = self.clock.today()
                if type(effective_date) is not date or effective_date < today:
                    raise VersionValidationError(
                        "Effective date must be today or a future calendar date"
                    )
                if not isinstance(release_note, str) or len(release_note) > MAX_RELEASE_NOTE:
                    raise VersionValidationError("Release note must not exceed 2000 characters")
                if dataset.kind == "mapping":
                    source = uow.versions.get(dataset.source_dataset_id, dataset.source_version_id)
                    if source is None or source.cancelled_at is not None:
                        raise VersionValidationError(
                            "Mapping requires its published source version."
                        )
                    if effective_date < source.effective_date:
                        raise VersionValidationError(
                            "Effective date cannot precede the source version."
                        )
                content, summary, number = self._prepare(uow, dataset, today)
                if any(
                    version.cancelled_at is None and version.effective_date == effective_date
                    for version in uow.versions.list_for_dataset(dataset.id)
                ):
                    raise VersionValidationError(
                        "A published version already begins on that effective date"
                    )
                if expected_review is not None:
                    diff._calendar_check(review_state)
                version = uow.versions.append(
                    dataset.id,
                    number,
                    now,
                    actor_id,
                    effective_date,
                    release_note,
                    content,
                    summary,
                )
                dataset.revision += 1
                uow.concepts.close(dataset.id)
                self._audit(uow, actor_id, version, "dataset.version_published")
                return version
        except PersistenceConflictError:
            raise VersionConflictError(
                "Publication could not be completed. Review and try again."
            ) from None

    def cancel(self, actor_id, dataset_id, version_id):
        try:
            with self.uow(write=True, serialized=True, translate_errors=True) as uow:
                dataset = self._target(uow, actor_id, dataset_id, write=True)
                version = next(
                    (v for v in uow.versions.list_for_dataset(dataset.id) if v.id == version_id),
                    None,
                )
                if version is None:
                    raise VersionNotFoundError("Dataset not found")
                now = self.clock.now()
                today = self.clock.today()
                if version.cancelled_at is not None or version.effective_date <= today:
                    raise VersionConflictError(
                        "Only a Scheduled version can be cancelled before its effective date"
                    )
                uow.versions.cancel(dataset.id, version.id, actor_id, now, today)
                dataset.revision += 1
                self._audit(uow, actor_id, version, "dataset.version_cancelled")
                return replace(version, cancelled_at=now, cancelling_actor_id=actor_id)
        except PersistenceConflictError:
            raise VersionConflictError(
                "Cancellation could not be completed. Review and try again."
            ) from None

    @staticmethod
    def _audit(uow, actor_id, version, action):
        record_audit(
            uow,
            actor_user_id=actor_id,
            object_type="dataset",
            object_id=version.dataset_id,
            action=action,
            details={
                "version_number": version.number,
                "effective_date": version.effective_date.isoformat(),
                "dataset_name": version.name,
                "field_count": version.field_count,
                "record_count": version.record_count,
            },
        )
