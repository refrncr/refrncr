"""Authoritative synchronous Dataset publication and cancellation use cases."""

from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import date
from itertools import chain
from types import MappingProxyType
from unicodedata import normalize

from refrncr.audit import record_audit
from refrncr.change_status import (
    DISPLAY_STATUSES,
    RECORD_STATUS_FILTERS,
    status_label,
)
from refrncr.concept_contracts import DISCARD_UNAVAILABLE, DiscardIdentity, DiscardReview
from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.diff_classification import classify_fields, summary_counts
from refrncr.hierarchy_contracts import hierarchy_node_paths
from refrncr.models import utc_now
from refrncr.ports import (
    DatasetBusinessKey,
    DatasetOverview,
    PersistenceConflictError,
    StoredRecord,
)
from refrncr.published_contracts import CurrentPublication, CurrentRead, decode_identifier
from refrncr.record_search import RecordSearchError, matches, search_term
from refrncr.services import (
    ConflictError,
    DatasetRecordService,
    NotFoundError,
    RecordPage,
    ValidationError,
    _valid_identity,
    normalize_dataset_field_name,
    normalize_dataset_name,
    validate_dataset_description,
    validate_dataset_field_flag,
    validate_dataset_field_key,
    validate_dataset_field_type,
    validate_dataset_key,
    validate_field_metadata,
)
from refrncr.version_contracts import (
    APPLICATION_TIMEZONE,
    MAX_RELEASE_NOTE,
    PublishedDataset,
    SnapshotContent,
    SnapshotField,
    SummaryCounts,
    VersionHistory,
    VersionRead,
    mapping_incomplete_message,
    version_states,
)


def validated_snapshot(uow, dataset):
    """Read and normalize without mutating working rows; reuse ingress validators."""
    try:
        return _validated_snapshot(uow, dataset)
    except DatatypeError:
        raise ValidationError("Dataset contains invalid stored values") from None


def _validated_snapshot(uow, dataset):
    validate_dataset_key(dataset.key)
    name = normalize_dataset_name(dataset.name)
    description = validate_dataset_description(dataset.description)
    if getattr(dataset, "kind", "reference") == "hierarchy":
        from refrncr.hierarchy import hierarchy_snapshot

        return hierarchy_snapshot(uow, dataset)
    fields = uow.dataset_fields.list_for_dataset(dataset.id)
    identifier = uow.dataset_business_keys.get(dataset.id)
    if not fields:
        raise ValidationError("Publication requires at least one Field")
    if not identifier.field_keys:
        raise ValidationError("Configure a Record identifier before publication")
    if getattr(dataset, "kind", "reference") == "mapping":
        if message := mapping_incomplete_message(fields):
            raise ValidationError(message)
    by_key = {f.key: f for f in fields}
    if len(by_key) != len(fields) or len({f.id for f in fields}) != len(fields):
        raise ValidationError("Invalid Field configuration")
    if sorted(f.display_position for f in fields) != list(range(1, len(fields) + 1)):
        raise ValidationError("Invalid Field order")
    if len(set(identifier.field_keys)) != len(identifier.field_keys):
        raise ValidationError("Invalid Record identifier configuration")
    for key in identifier.field_keys:
        field = by_key.get(key)
        if (
            field is None
            or not field.is_required
            or not get_datatype(field.data_type).identifier_eligible
        ):
            raise ValidationError("Invalid Record identifier configuration")
    snapshots = tuple(
        SnapshotField(
            f.id,
            dataset.id,
            validate_dataset_field_key(f.key),
            normalize_dataset_field_name(f.name),
            validate_dataset_field_type(f.data_type),
            validate_dataset_field_flag(f.is_required, "Required"),
            validate_dataset_field_flag(f.is_unique, "Unique"),
            f.display_position,
            f.key in identifier.field_keys,
            *validate_field_metadata(getattr(f, "definition", None), getattr(f, "unit", None)),
            getattr(f, "mapping_role", None),
            getattr(f, "source_field_key", None),
        )
        for f in fields
    )
    identifier = DatasetBusinessKey(dataset.id, tuple(sorted(identifier.field_keys)))
    records, seen_ids, seen_unique = [], set(), set()
    stored_identifiers = uow.dataset_records.identifier_keys(dataset.id)
    try:
        for record in uow.dataset_records.iterate_all(dataset.id):
            submitted = {}
            for value in record.values:
                field = by_key.get(value.field_key)
                if (
                    field is None
                    or field.id != value.field_id
                    or field.data_type != value.data_type
                    or value.field_key in submitted
                ):
                    raise ValidationError("Invalid stored Record configuration")
                datatype = get_datatype(value.data_type)
                decoded = datatype.from_key(value.canonical_key)
                submitted[field.key] = datatype.text(decoded)
            values, encoded = DatasetRecordService._validated_values(
                snapshots, identifier, submitted
            )
            if stored_identifiers.get(record.id) != encoded:
                raise ValidationError("Invalid stored Record identifier")
            if encoded in seen_ids:
                raise ValidationError("Duplicate Record identifier")
            seen_ids.add(encoded)
            for value in values:
                marker = value.field_id, value.canonical_key
                if value.is_unique:
                    if marker in seen_unique:
                        raise ValidationError("Duplicate unique Field value")
                    seen_unique.add(marker)
            # Record revisions and timestamps are intentionally not snapshot content.
            records.append(
                StoredRecord(
                    record.id, dataset.id, tuple(sorted(values, key=lambda v: v.field_id)), 1
                )
            )
    except DatatypeError:
        raise ValidationError("Dataset contains invalid stored values") from None
    return SnapshotContent(name, description, snapshots, tuple(sorted(records, key=lambda r: r.id)))


class DatasetVersionService:
    def __init__(self, uow, authorization, *, clock=utc_now):
        self.uow = uow
        self.authorization = authorization
        self.clock = clock

    def read_current(
        self,
        actor_id,
        key=None,
        *,
        offset=0,
        limit=50,
        records=False,
        identifier=None,
        unchanged=None,
        effective_date=None,
        version_number=None,
        hierarchy=False,
        allow_hierarchy=False,
    ):
        """Bounded published reads within the existing authorization/read snapshot.

        The optional predicate sees publication metadata after authorization/existence
        checks. It can avoid loading immutable content; it confers no authority.
        """
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise ValidationError("Invalid pagination")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("Invalid pagination")
        if effective_date is not None and type(effective_date) is not date:
            raise ValidationError("Invalid effective date")
        if version_number is not None and (
            type(version_number) is not int or not 1 <= version_number <= 2147483647
        ):
            raise ValidationError("Invalid version")
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            if key is None:
                allowed = self.authorization.viewable_resources(actor_id, uow=uow)
                dataset_ids = None if allowed.all_resources else allowed.resource_ids
                if effective_date is not None:
                    pairs, total = uow.versions.selected_page(
                        effective_date,
                        version_number=version_number,
                        offset=offset,
                        limit=limit,
                        dataset_ids=dataset_ids,
                        include_hierarchy=allow_hierarchy,
                    )
                elif version_number is not None:
                    pairs, total = uow.versions.version_page(
                        version_number,
                        offset=offset,
                        limit=limit,
                        dataset_ids=dataset_ids,
                        include_hierarchy=allow_hierarchy,
                    )
                else:
                    pairs, total = uow.versions.current_page(
                        self._today().date(),
                        offset=offset,
                        limit=limit,
                        dataset_ids=dataset_ids,
                        include_hierarchy=allow_hierarchy,
                    )
            else:
                try:
                    validate_dataset_key(key)
                except ValidationError:
                    raise NotFoundError("Dataset not found") from None
                dataset = uow.datasets.find_by_key(key)
                if (
                    dataset is None
                    or (hierarchy and dataset.kind != "hierarchy")
                    or (not allow_hierarchy and dataset.kind == "hierarchy")
                    or not self.authorization.can_view_dataset(actor_id, dataset.id, uow=uow)
                ):
                    raise NotFoundError("Dataset not found")
                if effective_date is not None:
                    version = uow.versions.selected_for_datasets((dataset.id,), effective_date).get(
                        dataset.id
                    )
                    if version is not None and version_number is not None:
                        if version.number != version_number:
                            raise ValidationError("Version was not effective on that date")
                elif version_number is not None:
                    version = uow.versions.version_for_datasets((dataset.id,), version_number).get(
                        dataset.id
                    )
                else:
                    version = uow.versions.current_for_datasets(
                        (dataset.id,), self._today().date()
                    ).get(dataset.id)
                if version is None:
                    raise NotFoundError("Dataset not found")
                pairs, total = ((dataset, version),), version.record_count
            result = CurrentRead(
                tuple(
                    CurrentPublication(
                        d.key, d.name, d.description, d.icon, d.kind, d.updated_at, v
                    )
                    for d, v in pairs
                ),
                total,
            )
            record_id = None
            if identifier is not None:
                try:
                    encoded = decode_identifier(identifier)
                except ValueError:
                    raise NotFoundError("Record not found") from None
                record_id = uow.versions.record_identity(version, encoded)
                if record_id is None:
                    raise NotFoundError("Record not found")
            if unchanged is not None and unchanged(result):
                return replace(result, not_modified=True)
            if key is None:
                return result
            fields = uow.versions.fields(version)
            rows = ()
            if record_id is not None:
                rows = tuple(uow.versions.iterate_records(version, record_ids=(record_id,)))
            elif hierarchy and not records:
                rows = tuple(
                    uow.versions.record_page(
                        version, fields, offset=0, limit=max(version.record_count, 1)
                    )
                )
            elif records:
                rows = uow.versions.record_page(version, fields, offset=offset, limit=limit)
            return replace(
                result,
                fields=fields,
                records=rows,
                hierarchy_nodes=(
                    uow.versions.hierarchy_nodes(version)
                    if hierarchy or dataset.kind == "hierarchy"
                    else ()
                ),
            )

    def _discard_review(self, uow, actor_id, dataset):
        from refrncr.services import _require_concept

        concept = _require_concept(uow, dataset.id)
        current = uow.versions.current_for_datasets((dataset.id,), self._today().date()).get(
            dataset.id
        )
        if current is None:
            raise ConflictError(DISCARD_UNAVAILABLE)
        summary = self._concept_context(uow, actor_id, dataset).summary
        identity = DiscardIdentity(
            dataset.id,
            concept.id,
            dataset.revision,
            concept.start_revision,
            concept.updated_at.isoformat(),
            actor_id,
            dataset.owner_team_id,
            current.id,
        )
        return DiscardReview(
            identity,
            dataset.name,
            summary.counts,
            len(summary.metadata),
            len(summary.hierarchy_changes),
        ), current

    def review_discard(self, actor_id, dataset_id):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = self._target(uow, actor_id, dataset_id)
            return self._discard_review(uow, actor_id, dataset)[0]

    def discard_concept(self, actor_id, dataset_id, expected):
        try:
            with self.uow(write=True, serialized=True, translate_errors=True) as uow:
                dataset = self._target(uow, actor_id, dataset_id, write=True)
                review, current = self._discard_review(uow, actor_id, dataset)
                if not isinstance(expected, DiscardIdentity) or expected != review.identity:
                    raise ConflictError(
                        "This Concept changed. Review the current changes "
                        "and confirm discard again."
                    )
                # Calendar activation has no writer/revision bump. Recheck after
                # computing counts, immediately before the destructive statements.
                current_now = uow.versions.current_for_datasets(
                    (dataset.id,), self._today().date()
                ).get(dataset.id)
                if current_now is None or current_now.id != expected.current_id:
                    raise ConflictError("Current changed. Review and confirm discard again.")
                uow.concepts.discard(dataset, current)
                dataset.revision += 1
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action="dataset.concept_discarded",
                    details={
                        "concept_id": expected.concept_id,
                        "metadata_changes": review.metadata_changes,
                        **asdict(review.counts),
                    },
                )
            return review
        except PersistenceConflictError:
            raise ConflictError("Discard could not be completed. Review and try again.") from None

    def create_concept(self, actor_id, dataset_id, *, uow=None):
        """The single concept-create operation, also used by Dataset creation."""
        try:
            with (
                nullcontext(uow)
                if uow is not None
                else self.uow(write=True, serialized=True, translate_errors=True) as work
            ):
                dataset = self._target(work, actor_id, dataset_id, write=True)
                if work.concepts.get(dataset.id) is not None:
                    raise ConflictError("This Dataset already has an active concept.")
                baseline = work.versions.latest_non_cancelled(dataset.id)
                now = self._today()
                if baseline is not None:
                    baseline_fields = work.versions.fields(baseline)
                    if len(
                        baseline_fields
                    ) != baseline.field_count or not work.version_diff.validate_snapshot(baseline):
                        raise ValidationError("Invalid published snapshot")
                    identifier = work.dataset_business_keys.get(dataset.id)
                    current_fields = tuple(
                        SnapshotField(
                            f.id,
                            dataset.id,
                            f.key,
                            f.name,
                            f.data_type,
                            f.is_required,
                            f.is_unique,
                            f.display_position,
                            f.key in identifier.field_keys,
                            getattr(f, "definition", None),
                            getattr(f, "unit", None),
                            getattr(f, "mapping_role", None),
                            getattr(f, "source_field_key", None),
                            getattr(f, "hierarchy_scope", None),
                        )
                        for f in work.dataset_fields.list_for_dataset(dataset.id)
                    )
                    changes = classify_fields(current_fields, baseline_fields)
                    if (
                        changes
                        or (
                            dataset.kind == "hierarchy"
                            and work.version_diff.hierarchy_changes(dataset.id, baseline.id)
                        )
                        or next(work.dataset_records.deleted_records(dataset.id), None) is not None
                        or any(work.version_diff.counts(dataset.id, baseline.id))
                    ):
                        work.concepts.initialize(dataset, baseline, now)
                dataset.revision += 1
                concept = work.concepts.create(
                    dataset.id, baseline.id if baseline else None, actor_id, now, dataset.revision
                )
                record_audit(
                    work,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action="dataset.concept_created",
                    details={
                        "baseline_version_number": baseline.number if baseline else 0,
                        "concept_revision": dataset.revision,
                    },
                )
            return concept
        except PersistenceConflictError:
            raise ConflictError("Concept could not be started. Reload and try again.") from None

    def _today(self):
        instant = self.clock()
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Version clock must return an aware instant")
        return instant.astimezone(APPLICATION_TIMEZONE)

    def _target(self, uow, actor_id, dataset_id, *, write=False):
        dataset = (
            uow.datasets.get(dataset_id, for_update=write) if _valid_identity(dataset_id) else None
        )
        if dataset is None or not self.authorization.can_maintain_dataset(
            actor_id, dataset.id, uow=uow
        ):
            raise NotFoundError("Dataset not found")
        return dataset

    @staticmethod
    def _published_context(dataset, version, owner_team_name, *, record_count=None):
        published = PublishedDataset(
            dataset.id,
            dataset.key,
            dataset.name,
            dataset.description,
            dataset.owner_team_id,
            owner_team_name,
            version.published_at,
            version.published_at,
            version.published_at,
            version.number,
            version.id,
            version.number,
            icon=dataset.icon,
            kind=dataset.kind,
        )
        return DatasetOverview(
            published,
            version.record_count if record_count is None else record_count,
            version.published_at,
            owner_team_name,
        )

    def overview(self, actor_id):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            maintenance = self.authorization.maintainable_resources(actor_id, uow=uow)
            rows = uow.datasets.current_overview(None)
            allowed = (
                {row.dataset.id for row in rows}
                if maintenance.all_resources
                else set(maintenance.resource_ids)
            )
            current = uow.versions.current_for_datasets(None, self._today().date())
            hierarchy_counts = uow.versions.hierarchy_node_counts(
                tuple(
                    current[row.dataset.id].id
                    for row in rows
                    if row.dataset.kind == "hierarchy" and row.dataset.id in current
                )
            )
            concepts = uow.concepts.list_for_datasets(tuple(allowed))
            result = []
            for row in rows:
                dataset = row.dataset
                if dataset.id in current:
                    item = self._published_context(
                        dataset,
                        current[dataset.id],
                        row.owner_team_name,
                        record_count=hierarchy_counts.get(current[dataset.id].id),
                    )
                elif dataset.id in allowed:
                    item = DatasetOverview(dataset, 0, dataset.created_at, row.owner_team_name)
                else:
                    continue
                result.append(
                    replace(
                        item,
                        concept_in_progress=dataset.id in concepts,
                        no_current=dataset.id not in current,
                    )
                )
            return sorted(result, key=lambda row: (row.dataset.name.casefold(), row.dataset.key))

    def _selection(
        self, uow, actor_id, key, version_id=None, *, prefer_concept=False, allow_unpublished=False
    ):
        try:
            key = validate_dataset_key(key)
        except ValidationError:
            raise NotFoundError("Dataset not found") from None
        dataset = uow.datasets.find_by_key(key)
        if dataset is None or not self.authorization.can_view_dataset(
            actor_id, dataset.id, uow=uow
        ):
            raise NotFoundError("Dataset not found")
        maintain = self.authorization.can_maintain_dataset(actor_id, dataset.id, uow=uow)
        if prefer_concept and maintain and version_id is None and uow.concepts.get(dataset.id):
            return dataset, None, maintain
        today = self._today().date()
        states = version_states(uow.versions.list_for_dataset(dataset.id), today)
        state = next(
            (
                state
                for state in states
                if (
                    state.version.id == version_id
                    if version_id is not None
                    else state.status == "Current"
                )
            ),
            None,
        )
        if state is None and allow_unpublished and maintain and version_id is None:
            return dataset, None, maintain
        if (
            state is None
            or state.status == "Cancelled"
            or (state.status == "Scheduled" and not maintain)
        ):
            raise NotFoundError("Dataset not found")
        return dataset, self._visible_state(state, maintain, today), maintain

    @staticmethod
    def _visible_state(state, maintain, today):
        # Do not disclose a private Scheduled successor through Current metadata.
        if not maintain and state.valid_until is not None and state.valid_until > today:
            return replace(state, valid_until=None)
        return state

    def _read_context(self, uow, dataset, state):
        if state is None:
            return uow.datasets.current_overview((dataset.id,))[0]
        owner = uow.teams.get(dataset.owner_team_id)
        count = (
            uow.versions.hierarchy_node_counts((state.version.id,))[state.version.id]
            if dataset.kind == "hierarchy"
            else state.version.record_count
        )
        return self._published_context(
            dataset, state.version, owner.name if owner else "", record_count=count
        )

    def context(self, actor_id, key):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, state, _maintain = self._selection(uow, actor_id, key, prefer_concept=True)
            return self._read_context(uow, dataset, state)

    def history(self, actor_id, key):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, selected, maintain = self._selection(
                uow, actor_id, key, allow_unpublished=True
            )
            concept = uow.concepts.get(dataset.id) if maintain else None
            concept_view = self._concept_context(uow, actor_id, dataset) if concept else None
            today = self._today().date()
            entries = tuple(
                self._visible_state(state, maintain, today)
                for state in version_states(uow.versions.list_for_dataset(dataset.id), today)
                if maintain or state.status in {"Current", "Expired"}
            )
            # Reuse REF-39's safe, batched historical actor-name projection.
            names = uow.audit_history.actor_names(
                tuple(
                    {state.version.publisher_id for state in entries if state.version.publisher_id}
                    | ({concept.creator_id} if concept and concept.creator_id else set())
                )
            )
            return VersionHistory(
                self._read_context(uow, dataset, selected)
                if selected
                else DatasetOverview(dataset, 0, dataset.created_at, dataset.owner_team_name),
                entries,
                MappingProxyType(names),
                maintain,
                concept,
                concept_view,
            )

    def _concept_context(self, uow, actor_id, dataset):
        from refrncr.concept_contracts import ConceptContext
        from refrncr.dataset_diff import DatasetDiffService

        diff = DatasetDiffService(self)
        target, identity, state, _current, _previous, changes = diff._load(
            uow, actor_id, dataset.id, allow_stale=True
        )
        concept = uow.concepts.get(dataset.id)
        names = uow.audit_history.actor_names((concept.creator_id,)) if concept.creator_id else {}
        return ConceptContext(
            concept,
            state,
            diff._summary(uow, target, identity, state, changes),
            changes,
            names.get(concept.creator_id, "Deleted user")
            if concept.creator_id
            else "System (migration)",
            dataset.id in uow.versions.current_for_datasets((dataset.id,), self._today().date()),
        )

    def concept_context(self, actor_id, key):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, _state, _maintain = self._selection(uow, actor_id, key, prefer_concept=True)
            return self._concept_context(uow, actor_id, dataset)

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
    ):
        if page is not None and (type(page) is not int or page < 1):
            raise ValidationError("Page must be a positive integer")
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
                from refrncr.hierarchy import hierarchy_leaf_records, hierarchy_validation_errors

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
                removed = tuple(record for record, _fields in pairs if record.id in baseline_ids)
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
                labels[record.id] = " · ".join(
                    get_datatype(values[key].data_type).text(values[key].value)
                    if key in values
                    else "—"
                    for key in keys
                )
                if dataset.kind == "hierarchy":
                    labels[record.id] = labels[record.id] or node_names[record.id]
                    if not keys:
                        return False, ((0, "string", node_names[record.id].casefold()),), record.id
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
            imported = {}
            if state is None:
                for offset in range(0, len(matching), 500):
                    projected.update(
                        uow.version_diff.record_states(
                            dataset.id,
                            concept.base_version_id,
                            tuple(r.id for r in matching[offset : offset + 500]),
                        )
                    )
                imported = (concept.import_result or {}).get("records", {})
            raw_states = {
                record.id: (
                    "Deleted"
                    if record.id in deleted_ids
                    else projected.get(
                        record.id,
                        imported.get(record.id) if imported.get(record.id) != "Deleted" else None,
                    )
                )
                for record in matching
            }
            status_counts = tuple(
                sum(raw_states[record.id] == label for record in matching)
                for label in DISPLAY_STATUSES
            )
            ordered = matching
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
            )

    def review(self, actor_id, dataset_id):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = self._target(uow, actor_id, dataset_id)
            content, summary, number = self._prepare(uow, dataset, self._today().date())
            return dataset.revision, number, content, summary

    def _prepare(self, uow, dataset, today):
        from refrncr.services import _require_concept

        concept = _require_concept(uow, dataset.id)
        versions = uow.versions.list_for_dataset(dataset.id)
        baseline = next((v for v in versions if v.id == concept.base_version_id), None)
        if concept.base_version_id is not None and (
            baseline is None or baseline.cancelled_at is not None
        ):
            raise ConflictError(
                "The concept basis was cancelled. Publication is blocked; "
                "your changes are preserved."
            )
        if any(s.status == "Scheduled" for s in version_states(versions, today)):
            raise ConflictError("Cancel the Scheduled version or wait until it is Current")
        content = validated_snapshot(uow, dataset)
        latest = next((v for v in versions if v.cancelled_at is None), None)
        if (latest.id if latest else None) != concept.base_version_id:
            raise ConflictError(
                "The concept basis is no longer the latest version. Your changes are preserved."
            )
        if latest is None:
            summary = SummaryCounts(
                fields_added=len(content.fields), records_added=len(content.records)
            )
            changed = True
        else:
            if not uow.version_diff.validate_snapshot(latest):
                raise ValidationError("Invalid published snapshot")
            fields = classify_fields(content.fields, uow.versions.fields(latest))
            summary = summary_counts(fields, uow.version_diff.counts(dataset.id, latest.id))
            changed = any(getattr(summary, key) for key in SummaryCounts.__dataclass_fields__)
            if dataset.kind == "hierarchy":
                changed = changed or bool(uow.version_diff.hierarchy_changes(dataset.id, latest.id))
        if not changed:
            raise ConflictError("Dataset content matches the latest published version")
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
                    raise ConflictError("Dataset changed. Review the current content again.")
                if expected_review is not None:
                    # Optional REF-84 context, checked under this same publication lock.
                    # Ordinary REF-83 publication remains independent of detailed review.
                    from refrncr.dataset_diff import DatasetDiffService

                    diff = DatasetDiffService(self)
                    review_state = diff._load(uow, actor_id, dataset_id, expected_review)[2]
                now = self._today()
                if type(effective_date) is not date or effective_date < now.date():
                    raise ValidationError("Effective date must be today or a future calendar date")
                if not isinstance(release_note, str) or len(release_note) > MAX_RELEASE_NOTE:
                    raise ValidationError("Release note must not exceed 2000 characters")
                content, summary, number = self._prepare(uow, dataset, now.date())
                if any(
                    version.cancelled_at is None and version.effective_date == effective_date
                    for version in uow.versions.list_for_dataset(dataset.id)
                ):
                    raise ValidationError(
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
            raise ConflictError(
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
                    raise NotFoundError("Dataset not found")
                now = self._today()
                if version.cancelled_at is not None or version.effective_date <= now.date():
                    raise ConflictError(
                        "Only a Scheduled version can be cancelled before its effective date"
                    )
                uow.versions.cancel(dataset.id, version.id, actor_id, now, now.date())
                dataset.revision += 1
                self._audit(uow, actor_id, version, "dataset.version_cancelled")
                return replace(version, cancelled_at=now, cancelling_actor_id=actor_id)
        except PersistenceConflictError:
            raise ConflictError(
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
