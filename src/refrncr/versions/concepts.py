"""Authoritative synchronous Dataset publication and cancellation use cases."""

from contextlib import nullcontext
from dataclasses import asdict, replace
from types import MappingProxyType

from refrncr.audit import record_audit
from refrncr.datasets.dataset_diff import DatasetDiffService
from refrncr.datasets.mapping_updates import reconcile_source
from refrncr.datasets.shared import validate_dataset_key
from refrncr.domain import advance_concept, require_concept, valid_identity
from refrncr.errors import ValidationError
from refrncr.ports import (
    DatasetOverview,
    PersistenceConflictError,
)
from refrncr.versions.concept_contracts import (
    DISCARD_UNAVAILABLE,
    ConceptContext,
    DiscardIdentity,
    DiscardReview,
)
from refrncr.versions.diff_classification import classify_fields
from refrncr.versions.errors import (
    VersionConflictError,
    VersionNotFoundError,
    VersionValidationError,
)
from refrncr.versions.version_contracts import (
    PublishedDataset,
    SnapshotField,
    VersionHistory,
    version_states,
)


class DatasetVersionConceptMixin:
    def _discard_review(self, uow, actor_id, dataset):
        concept = require_concept(uow, dataset.id)
        current = uow.versions.current_for_datasets((dataset.id,), self.clock.today()).get(
            dataset.id
        )
        if current is None:
            raise VersionConflictError(DISCARD_UNAVAILABLE)
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
                    raise VersionConflictError(
                        "This Concept changed. Review the current changes "
                        "and confirm discard again."
                    )
                # Calendar activation has no writer/revision bump. Recheck after
                # computing counts, immediately before the destructive statements.
                current_now = uow.versions.current_for_datasets(
                    (dataset.id,), self.clock.today()
                ).get(dataset.id)
                if current_now is None or current_now.id != expected.current_id:
                    raise VersionConflictError("Current changed. Review and confirm discard again.")
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
            raise VersionConflictError(
                "Discard could not be completed. Review and try again."
            ) from None

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
                    raise VersionConflictError("This Dataset already has an active concept.")
                baseline = work.versions.latest_non_cancelled(dataset.id)
                now = self.clock.now()
                if baseline is not None:
                    dataset.source_version_id = baseline.source_version_id
                    baseline_fields = work.versions.fields(baseline)
                    if len(
                        baseline_fields
                    ) != baseline.field_count or not work.version_diff.validate_snapshot(baseline):
                        raise VersionValidationError("Invalid published snapshot")
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
            raise VersionConflictError(
                "Concept could not be started. Reload and try again."
            ) from None

    def update_mapping_source(self, actor_id, dataset_id, expected_revision):
        """Explicitly upgrade to the latest non-cancelled publication, atomically."""
        try:
            with self.uow(write=True, serialized=True, translate_errors=True) as uow:
                dataset = self._target(uow, actor_id, dataset_id, write=True)
                if type(expected_revision) is not int or dataset.revision != expected_revision:
                    raise VersionConflictError("Mapping changed. Reload and try again.")
                if dataset.kind != "mapping":
                    raise VersionValidationError("Only mappings can update a source version.")
                # Serialize against publication/cancellation of the source as well.
                uow.datasets.get(dataset.source_dataset_id, for_update=True)
                source = uow.versions.latest_non_cancelled(dataset.source_dataset_id)
                if uow.concepts.get(dataset.id) is None:
                    self.create_concept(actor_id, dataset.id, uow=uow)
                previous = uow.versions.get(dataset.source_dataset_id, dataset.source_version_id)
                if source is None or previous is None:
                    raise VersionValidationError("Mapping requires its published source version.")
                if source.number <= previous.number:
                    return uow.concepts.get(dataset.id)
                notes = reconcile_source(uow, dataset, source)
                dataset.source_version_id = source.id
                dataset.content_updated_at = uow.now()
                concept = uow.concepts.get(dataset.id)
                uow.concepts.record_mutation(
                    dataset.id,
                    import_result={},
                    source_update_notes=[*(concept.source_update_notes or []), *notes],
                )
                advance_concept(uow, dataset)
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action="dataset.mapping_source_updated",
                    details={"previous_version": previous.number, "source_version": source.number},
                )
                return uow.concepts.get(dataset.id)
        except PersistenceConflictError:
            raise VersionConflictError(
                "Source update could not be completed. Reload and try again."
            ) from None

    def _target(self, uow, actor_id, dataset_id, *, write=False):
        dataset = (
            uow.datasets.get(dataset_id, for_update=write) if valid_identity(dataset_id) else None
        )
        if dataset is None or not self.authorization.can_maintain_dataset(
            actor_id, dataset.id, uow=uow
        ):
            raise VersionNotFoundError("Dataset not found")
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
            current = uow.versions.current_for_datasets(None, self.clock.today())
            hierarchy_counts = uow.versions.hierarchy_node_counts(
                tuple(
                    current[row.dataset.id].id
                    for row in rows
                    if row.dataset.kind == "hierarchy" and row.dataset.id in current
                )
            )
            concepts = uow.concepts.list_for_datasets(tuple(allowed))
            mapping_sources = {
                row.dataset.id: (
                    current[row.dataset.id].source_version_id
                    if row.dataset.id in current
                    else row.dataset.source_version_id
                )
                for row in rows
                if row.dataset.kind == "mapping"
                and (row.dataset.id in current or row.dataset.id in allowed)
            }
            updates = uow.versions.source_updates(tuple(mapping_sources.values()))
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
                        source_update_available=mapping_sources.get(dataset.id) in updates,
                    )
                )
            return sorted(result, key=lambda row: (row.dataset.name.casefold(), row.dataset.key))

    def _selection(
        self, uow, actor_id, key, version_id=None, *, prefer_concept=False, allow_unpublished=False
    ):
        try:
            key = validate_dataset_key(key)
        except ValidationError:
            raise VersionNotFoundError("Dataset not found") from None
        dataset = uow.datasets.find_by_key(key)
        if dataset is None or not self.authorization.can_view_dataset(
            actor_id, dataset.id, uow=uow
        ):
            raise VersionNotFoundError("Dataset not found")
        maintain = self.authorization.can_maintain_dataset(actor_id, dataset.id, uow=uow)
        if prefer_concept and maintain and version_id is None and uow.concepts.get(dataset.id):
            return dataset, None, maintain
        today = self.clock.today()
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
            raise VersionNotFoundError("Dataset not found")
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
            today = self.clock.today()
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
            dataset.id in uow.versions.current_for_datasets((dataset.id,), self.clock.today()),
        )

    def concept_context(self, actor_id, key):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset, _state, _maintain = self._selection(uow, actor_id, key, prefer_concept=True)
            return self._concept_context(uow, actor_id, dataset)
