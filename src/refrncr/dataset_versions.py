"""Authoritative synchronous Dataset publication and cancellation use cases."""

from dataclasses import replace
from datetime import date
from types import MappingProxyType

from refrncr.audit import record_audit
from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.models import utc_now
from refrncr.ports import (
    DatasetBusinessKey,
    DatasetOverview,
    PersistenceConflictError,
    StoredRecord,
)
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
    fields = uow.dataset_fields.list_for_dataset(dataset.id)
    identifier = uow.dataset_business_keys.get(dataset.id)
    if not fields:
        raise ValidationError("Publication requires at least one Field")
    if not identifier.field_keys:
        raise ValidationError("Configure a Record identifier before publication")
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


def compare_snapshots(current, previous):
    def counts(new, old):
        return (
            len(new.keys() - old.keys()),
            sum(new[k] != old[k] for k in new.keys() & old.keys()),
            len(old.keys() - new.keys()),
        )

    def fields(content):
        return {f.id: f for f in content.fields} if content else {}

    def records(content):
        return (
            {
                r.id: tuple(sorted((v.field_id, v.data_type, v.canonical_key) for v in r.values))
                for r in content.records
            }
            if content
            else {}
        )

    summary = SummaryCounts(
        *counts(fields(current), fields(previous)), *counts(records(current), records(previous))
    )
    changed = (
        previous is None
        or current.name != previous.name
        or current.description != previous.description
        or any(getattr(summary, key) for key in SummaryCounts.__dataclass_fields__)
    )
    return changed, summary


class DatasetVersionService:
    def __init__(self, uow, authorization, *, clock=utc_now):
        self.uow = uow
        self.authorization = authorization
        self.clock = clock

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
    def _published_context(dataset, version, owner_team_name):
        published = PublishedDataset(
            dataset.id,
            dataset.key,
            version.name,
            version.description,
            dataset.owner_team_id,
            owner_team_name,
            version.published_at,
            version.published_at,
            version.published_at,
            version.number,
            version.id,
            version.number,
        )
        return DatasetOverview(
            published, version.record_count, version.published_at, owner_team_name
        )

    def overview(self, actor_id):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            maintenance = self.authorization.maintainable_resources(actor_id, uow=uow)
            rows = uow.datasets.current_overview(None)
            if maintenance.all_resources:
                return rows
            allowed = set(maintenance.resource_ids)
            current = uow.versions.current_for_datasets(None, self._today().date())
            result = [
                row
                if row.dataset.id in allowed
                else self._published_context(
                    row.dataset, current[row.dataset.id], row.owner_team_name
                )
                for row in rows
                if row.dataset.id in allowed or row.dataset.id in current
            ]
            return sorted(result, key=lambda row: (row.dataset.name.casefold(), row.dataset.key))

    def _selection(self, uow, actor_id, key, version_id=None, *, prefer_concept=False):
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
        if prefer_concept and maintain and version_id is None:
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
        return self._published_context(dataset, state.version, owner.name if owner else "")

    def context(self, actor_id, key):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, state, _maintain = self._selection(uow, actor_id, key, prefer_concept=True)
            return self._read_context(uow, dataset, state)

    def history(self, actor_id, key):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, selected, maintain = self._selection(uow, actor_id, key, prefer_concept=True)
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
                )
            )
            return VersionHistory(
                self._read_context(uow, dataset, selected),
                entries,
                MappingProxyType(names),
                maintain,
            )

    def read(self, actor_id, key, version_id=None, page=1, *, prefer_concept=False):
        if page is not None and (type(page) is not int or page < 1):
            raise ValidationError("Page must be a positive integer")
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, state, maintain = self._selection(
                uow, actor_id, key, version_id, prefer_concept=prefer_concept
            )
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
            ordered = sorted(
                records,
                key=lambda record: DatasetRecordService._record_sort_key(
                    record, identifier.field_keys
                ),
            )
            page_size = DatasetRecordService.PAGE_SIZE
            start = ((page or 1) - 1) * page_size
            record_page = RecordPage(
                tuple(ordered[start : start + page_size]),
                page or 1,
                page_size,
                context.record_count,
            )
            export = self.authorization.can_export_dataset(actor_id, dataset.id, uow=uow)
            return VersionRead(
                context,
                fields,
                identifier,
                record_page,
                state,
                maintain,
                export and (state is None or state.status in {"Current", "Expired"}),
            )

    def review(self, actor_id, dataset_id):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = self._target(uow, actor_id, dataset_id)
            content, summary, number = self._prepare(uow, dataset, self._today().date())
            return dataset.revision, number, content, summary

    def _prepare(self, uow, dataset, today):
        versions = uow.versions.list_for_dataset(dataset.id)
        if any(s.status == "Scheduled" for s in version_states(versions, today)):
            raise ConflictError("Cancel the Scheduled version or wait until it is Current")
        content = validated_snapshot(uow, dataset)
        latest = next((v for v in versions if v.cancelled_at is None), None)
        previous = uow.versions.content(latest) if latest else None
        changed, summary = compare_snapshots(content, previous)
        if not changed:
            raise ConflictError("Dataset content matches the latest published version")
        return content, summary, max((v.number for v in versions), default=0) + 1

    def publish(self, actor_id, dataset_id, effective_date, release_note, expected_revision):
        try:
            with self.uow(write=True, serialized=True, translate_errors=True) as uow:
                dataset = self._target(uow, actor_id, dataset_id, write=True)
                if type(expected_revision) is not int or expected_revision != dataset.revision:
                    raise ConflictError("Dataset changed. Review the current content again.")
                now = self._today()
                if type(effective_date) is not date or effective_date < now.date():
                    raise ValidationError("Effective date must be today or a future calendar date")
                if not isinstance(release_note, str) or len(release_note) > MAX_RELEASE_NOTE:
                    raise ValidationError("Release note must not exceed 2000 characters")
                content, summary, number = self._prepare(uow, dataset, now.date())
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
