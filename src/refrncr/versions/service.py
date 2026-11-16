"""Authoritative synchronous Dataset publication and cancellation use cases."""

from dataclasses import replace
from datetime import date

from refrncr.audit import record_audit
from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.datasets import export as dataset_export
from refrncr.datasets import history_export
from refrncr.datasets.datatypes import DatatypeError, get_datatype
from refrncr.datasets.records import DatasetRecordService
from refrncr.datasets.shared import (
    normalize_dataset_field_name,
    normalize_dataset_name,
    validate_dataset_description,
    validate_dataset_field_flag,
    validate_dataset_field_key,
    validate_dataset_field_type,
    validate_dataset_key,
    validate_field_metadata,
)
from refrncr.errors import ValidationError
from refrncr.hierarchy import (
    hierarchy_snapshot,
)
from refrncr.ports import (
    DatasetBusinessKey,
    StoredRecord,
)
from refrncr.versions.concepts import DatasetVersionConceptMixin
from refrncr.versions.errors import VersionNotFoundError, VersionValidationError
from refrncr.versions.lifecycle import DatasetVersionLifecycleMixin
from refrncr.versions.published_contracts import (
    CurrentPublication,
    CurrentRead,
    decode_identifier,
    mapping_identifier,
)
from refrncr.versions.record_intervals import HistoryRead, derive_intervals
from refrncr.versions.version_contracts import (
    SnapshotContent,
    SnapshotField,
    mapping_incomplete_message,
    version_states,
)


def validated_snapshot(uow, dataset):
    """Read and normalize without mutating working rows; reuse ingress validators."""
    try:
        return _validated_snapshot(uow, dataset)
    except DatatypeError:
        raise VersionValidationError("Dataset contains invalid stored values") from None


def _validated_snapshot(uow, dataset):
    validate_dataset_key(dataset.key)
    name = normalize_dataset_name(dataset.name)
    description = validate_dataset_description(dataset.description)
    if getattr(dataset, "kind", "reference") == "hierarchy":
        return hierarchy_snapshot(uow, dataset)
    fields = uow.dataset_fields.list_for_dataset(dataset.id)
    identifier = uow.dataset_business_keys.get(dataset.id)
    if not fields:
        raise VersionValidationError("Publication requires at least one Field")
    if not identifier.field_keys:
        raise VersionValidationError("Configure a Record identifier before publication")
    if getattr(dataset, "kind", "reference") == "mapping":
        if message := mapping_incomplete_message(fields):
            raise VersionValidationError(message)
    by_key = {f.key: f for f in fields}
    if len(by_key) != len(fields) or len({f.id for f in fields}) != len(fields):
        raise VersionValidationError("Invalid Field configuration")
    if sorted(f.display_position for f in fields) != list(range(1, len(fields) + 1)):
        raise VersionValidationError("Invalid Field order")
    if len(set(identifier.field_keys)) != len(identifier.field_keys):
        raise VersionValidationError("Invalid Record identifier configuration")
    for key in identifier.field_keys:
        field = by_key.get(key)
        if (
            field is None
            or not field.is_required
            or not get_datatype(field.data_type).identifier_eligible
        ):
            raise VersionValidationError("Invalid Record identifier configuration")
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
                    raise VersionValidationError("Invalid stored Record configuration")
                datatype = get_datatype(value.data_type)
                decoded = datatype.from_key(value.canonical_key)
                submitted[field.key] = datatype.text(decoded)
            values, encoded = DatasetRecordService._validated_values(
                snapshots, identifier, submitted
            )
            if stored_identifiers.get(record.id) != encoded:
                raise VersionValidationError("Invalid stored Record identifier")
            if encoded in seen_ids:
                raise VersionValidationError("Duplicate Record identifier")
            seen_ids.add(encoded)
            for value in values:
                marker = value.field_id, value.canonical_key
                if value.is_unique:
                    if marker in seen_unique:
                        raise VersionValidationError("Duplicate unique Field value")
                    seen_unique.add(marker)
            # Record revisions and timestamps are intentionally not snapshot content.
            records.append(
                StoredRecord(
                    record.id, dataset.id, tuple(sorted(values, key=lambda v: v.field_id)), 1
                )
            )
    except DatatypeError:
        raise VersionValidationError("Dataset contains invalid stored values") from None
    return SnapshotContent(
        name,
        description,
        snapshots,
        tuple(sorted(records, key=lambda r: r.id)),
        source_version_id=getattr(dataset, "source_version_id", None),
    )


class DatasetVersionService(
    DatasetVersionConceptMixin,
    DatasetVersionLifecycleMixin,
):
    def __init__(self, uow, authorization, *, clock: Clock | None = None):
        self.uow = uow
        self.authorization = authorization
        self.clock = clock or getattr(uow, "clock", SYSTEM_CLOCK)

    @staticmethod
    def _snapshot(uow, dataset):
        return validated_snapshot(uow, dataset)

    @staticmethod
    def _validate_current_read(offset, limit, effective_date, version_number):
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise VersionValidationError("Invalid pagination")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise VersionValidationError("Invalid pagination")
        if effective_date is not None and type(effective_date) is not date:
            raise VersionValidationError("Invalid effective date")
        if version_number is not None and (
            type(version_number) is not int or not 1 <= version_number <= 2147483647
        ):
            raise VersionValidationError("Invalid version")

    def _current_page(
        self, uow, actor_id, effective_date, version_number, offset, limit, allow_hierarchy
    ):
        allowed = self.authorization.exportable_resources(actor_id, uow=uow)
        dataset_ids = None if allowed.all_resources else allowed.resource_ids
        options = {
            "offset": offset,
            "limit": limit,
            "dataset_ids": dataset_ids,
            "include_hierarchy": allow_hierarchy,
        }
        if effective_date is not None:
            return uow.versions.selected_page(
                effective_date, version_number=version_number, **options
            )
        if version_number is not None:
            return uow.versions.version_page(version_number, **options)
        return uow.versions.current_page(self.clock.today(), **options)

    def _current_version(self, uow, dataset, effective_date, version_number):
        if effective_date is not None:
            version = uow.versions.selected_for_datasets((dataset.id,), effective_date).get(
                dataset.id
            )
            if (
                version is not None
                and version_number is not None
                and version.number != version_number
            ):
                raise VersionValidationError("Version was not effective on that date")
            return version
        if version_number is not None:
            return uow.versions.version_for_datasets((dataset.id,), version_number).get(dataset.id)
        return uow.versions.current_for_datasets((dataset.id,), self.clock.today()).get(dataset.id)

    def read_current(
        self,
        actor_id,
        key=None,
        *,
        offset=0,
        limit=50,
        records=False,
        identifier=None,
        source=None,
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
        self._validate_current_read(offset, limit, effective_date, version_number)
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            if key is None:
                pairs, total = self._current_page(
                    uow,
                    actor_id,
                    effective_date,
                    version_number,
                    offset,
                    limit,
                    allow_hierarchy,
                )
            else:
                try:
                    validate_dataset_key(key)
                except ValidationError:
                    raise VersionNotFoundError("Dataset not found") from None
                dataset = uow.datasets.find_by_key(key)
                if (
                    dataset is None
                    or (source is not None and dataset.kind != "mapping")
                    or (hierarchy and dataset.kind != "hierarchy")
                    or (not allow_hierarchy and dataset.kind == "hierarchy")
                    or not self.authorization.can_export_dataset(actor_id, dataset.id, uow=uow)
                ):
                    raise VersionNotFoundError("Dataset not found")
                version = self._current_version(uow, dataset, effective_date, version_number)
                if version is None:
                    raise VersionNotFoundError("Dataset not found")
                pairs, total = ((dataset, version),), version.record_count
            sources = uow.versions.source_references(
                tuple(v.source_version_id for d, v in pairs if d.kind == "mapping")
            )
            validity = {
                state.version.id: state.valid_until
                for versions in uow.versions.list_for_datasets(
                    tuple(dataset.id for dataset, _version in pairs)
                ).values()
                for state in version_states(versions, self.clock.today())
            }
            result = CurrentRead(
                tuple(
                    CurrentPublication(
                        d.key,
                        d.name,
                        d.description,
                        d.icon,
                        d.kind,
                        d.updated_at,
                        v,
                        validity[v.id],
                        sources.get(v.source_version_id) if d.kind == "mapping" else None,
                    )
                    for d, v in pairs
                ),
                total,
            )
            record_id = None
            fields = None
            if source is not None:
                fields = uow.versions.fields(version)
                try:
                    identifier = mapping_identifier(source, fields)
                except DatatypeError:
                    raise VersionValidationError("Invalid source identifier") from None
            if identifier is not None:
                try:
                    encoded = decode_identifier(
                        identifier, allow_hierarchy=dataset.kind == "hierarchy"
                    )
                except ValueError:
                    raise VersionNotFoundError("Record not found") from None
                # Match only the selected snapshot's key. A synthetic key is absent
                # when that version uses business identifiers, even for the same leaf.
                record_id = uow.versions.record_identity(version, encoded)
                if record_id is None:
                    raise VersionNotFoundError("Record not found")
            if unchanged is not None and unchanged(result):
                return replace(result, not_modified=True)
            if key is None:
                return result
            if fields is None:
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

    def read_versions(self, actor_id, key, *, offset=0, limit=50, unchanged=None):
        """List non-cancelled publications in effective-period order."""
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise VersionValidationError("Invalid pagination")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise VersionValidationError("Invalid pagination")
        try:
            key = validate_dataset_key(key)
        except ValidationError:
            raise VersionNotFoundError("Dataset not found") from None
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.find_by_key(key)
            if dataset is None or not self.authorization.can_export_dataset(
                actor_id, dataset.id, uow=uow
            ):
                raise VersionNotFoundError("Dataset not found")
            states = tuple(
                state
                for state in version_states(
                    uow.versions.list_for_dataset(dataset.id), self.clock.today()
                )
                if state.status != "Cancelled"
            )
            if not states:
                raise VersionNotFoundError("Dataset not found")
            states = tuple(
                sorted(
                    states,
                    key=lambda state: (state.version.effective_date, state.version.number),
                )
            )
            total = len(states)
            states = states[offset : offset + limit]
            sources = uow.versions.source_references(
                tuple(
                    state.version.source_version_id
                    for state in states
                    if dataset.kind == "mapping" and state.version.source_version_id
                )
            )
            result = CurrentRead(
                tuple(
                    CurrentPublication(
                        dataset.key,
                        dataset.name,
                        dataset.description,
                        dataset.icon,
                        dataset.kind,
                        dataset.updated_at,
                        state.version,
                        state.valid_until,
                        sources.get(state.version.source_version_id),
                        state.status,
                    )
                    for state in states
                ),
                total,
            )
            if unchanged is not None and unchanged(result):
                return replace(result, not_modified=True)
            return result

    def read_history(
        self, actor_id, key, *, valid_from=None, valid_to=None, offset=0, limit=50, unchanged=None
    ):
        """Derive record-state intervals from every non-cancelled publication."""
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise VersionValidationError("Invalid pagination")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise VersionValidationError("Invalid pagination")
        if (valid_from is not None and not isinstance(valid_from, date)) or (
            valid_to is not None and not isinstance(valid_to, date)
        ):
            raise VersionValidationError("Invalid period")
        if valid_from is not None and valid_to is not None and valid_from >= valid_to:
            raise VersionValidationError("Invalid period")
        try:
            key = validate_dataset_key(key)
        except ValidationError:
            raise VersionNotFoundError("Dataset not found") from None
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.find_by_key(key)
            if (
                dataset is None
                or dataset.kind == "hierarchy"
                or not self.authorization.can_export_dataset(actor_id, dataset.id, uow=uow)
            ):
                raise VersionNotFoundError("Dataset not found")
            states = tuple(
                sorted(
                    (
                        state
                        for state in version_states(
                            uow.versions.list_for_dataset(dataset.id), self.clock.today()
                        )
                        if state.status != "Cancelled"
                    ),
                    key=lambda state: (state.version.effective_date, state.version.number),
                )
            )
            if not states:
                raise VersionNotFoundError("Dataset not found")
            publications = tuple(
                CurrentPublication(
                    dataset.key,
                    dataset.name,
                    dataset.description,
                    dataset.icon,
                    dataset.kind,
                    dataset.updated_at,
                    state.version,
                    state.valid_until,
                    None,
                    state.status,
                )
                for state in states
            )
            intervals = derive_intervals(
                (
                    (
                        state,
                        fields,
                        uow.versions.iterate_records(state.version),
                    )
                    for state in states
                    for fields in (uow.versions.fields(state.version),)
                ),
                lower=valid_from,
                upper=valid_to,
                offset=offset,
                limit=limit,
            )
            result = HistoryRead(
                publications, intervals.rows, intervals.field_keys, intervals.total
            )
            if unchanged is not None and unchanged(result):
                return replace(result, not_modified=True)
            return result

    def export_history(
        self,
        actor_id,
        key,
        format_name,
        *,
        valid_from=None,
        valid_to=None,
        open_end=False,
    ):
        """Export the same derived interval rows as the history API."""
        if format_name not in {"csv", "xlsx"} or type(open_end) is not bool:
            raise VersionValidationError("Invalid period export")
        if (valid_from is not None and not isinstance(valid_from, date)) or (
            valid_to is not None and not isinstance(valid_to, date)
        ):
            raise VersionValidationError("Invalid period export")
        if valid_from is not None and valid_to is not None and valid_from >= valid_to:
            raise VersionValidationError("Invalid period export")
        try:
            key = validate_dataset_key(key)
        except ValidationError:
            raise VersionNotFoundError("Dataset not found") from None
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.find_by_key(key)
            if (
                dataset is None
                or dataset.kind == "hierarchy"
                or not self.authorization.can_export_dataset(actor_id, dataset.id, uow=uow)
            ):
                raise VersionNotFoundError("Dataset not found")
            states = tuple(
                sorted(
                    (
                        state
                        for state in version_states(
                            uow.versions.list_for_dataset(dataset.id), self.clock.today()
                        )
                        if state.status != "Cancelled"
                    ),
                    key=lambda state: (state.version.effective_date, state.version.number),
                )
            )
            if not states:
                raise VersionNotFoundError("Dataset not found")
            intervals = derive_intervals(
                (
                    (state, fields, uow.versions.iterate_records(state.version))
                    for state in states
                    for fields in (uow.versions.fields(state.version),)
                ),
                lower=valid_from,
                upper=valid_to,
                limit=2147483647,
            )
            rows, field_keys = intervals.rows, intervals.field_keys
            dataset_id = dataset.id
        artifact = history_export.generate_history_export(
            format_name, field_keys, rows, key, open_end=open_end
        )
        dataset_export.verify_artifact_readable(artifact)
        try:
            with self.uow(write=True, translate_errors=True) as uow:
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset_id,
                    action="dataset.history_export_requested",
                    details={
                        "format": format_name,
                        "valid_from": valid_from.isoformat() if valid_from else "open",
                        "valid_to": valid_to.isoformat() if valid_to else "open",
                        "open_end": "9999-12-31" if open_end else "empty",
                    },
                )
        except BaseException:
            artifact.content.close()
            raise
        return artifact
