"""Revision-bound, administrator-only, on-demand concept review."""

from dataclasses import asdict
from heapq import nlargest, nsmallest
from unicodedata import normalize

from refrncr.audit import record_audit
from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.diff_classification import classify_fields, summary_counts
from refrncr.diff_contracts import (
    MAX_SEARCH,
    PAGE_SIZE,
    STATUSES,
    DiffCriteria,
    DiffPage,
    DiffSummary,
    RecordChange,
    RecordDetail,
    ReviewIdentity,
)
from refrncr.ports import PersistenceConflictError
from refrncr.services import ConflictError, NotFoundError, ValidationError, _valid_identity
from refrncr.version_contracts import SnapshotField, version_states

STALE_MESSAGE = "This Dataset changed. Reload the review to see its current changes."


class DatasetDiffService:
    def __init__(self, versions):
        self.versions = versions
        self.uow = versions.uow

    def _load(self, uow, actor_id, dataset_id, expected=None, *, write=False, allow_stale=False):
        dataset = self.versions._target(uow, actor_id, dataset_id, write=write)
        from refrncr.services import _require_concept

        concept = _require_concept(uow, dataset_id)
        latest = (
            uow.versions.get(dataset_id, concept.base_version_id)
            if concept.base_version_id
            else None
        )
        if concept.base_version_id is not None and latest is None:
            raise ConflictError("The concept basis is unavailable. Your changes are preserved.")
        state = version_states((latest,), self.versions._today().date())[0] if latest else None
        if state and state.status == "Cancelled" and not allow_stale:
            raise ConflictError(
                "The concept basis was cancelled. Your changes are preserved; "
                "publication is blocked."
            )
        identity = ReviewIdentity(
            dataset.id,
            actor_id,
            dataset.revision,
            latest.id if latest else None,
            latest.number if latest else 0,
            state.status if state else "Unpublished",
            concept.id,
        )
        if expected is not None and expected != identity:
            raise ConflictError(STALE_MESSAGE)
        identifier = uow.dataset_business_keys.get(dataset_id)
        current = tuple(
            SnapshotField(
                f.id,
                dataset_id,
                f.key,
                f.name,
                f.data_type,
                f.is_required,
                f.is_unique,
                f.display_position,
                f.key in identifier.field_keys,
                getattr(f, "definition", None),
                getattr(f, "unit", None),
            )
            for f in uow.dataset_fields.list_for_dataset(dataset_id)
        )
        previous = uow.versions.fields(latest) if latest else ()
        if latest and len(previous) != latest.field_count:
            raise ValidationError("Invalid published snapshot")
        changes = classify_fields(current, previous)
        return dataset, identity, state, current, previous, changes

    def _calendar_check(self, state):
        # A Scheduled baseline becoming Current during a long query is stale too.
        if state is None:
            return
        current = version_states((state.version,), self.versions._today().date())[0]
        if current.status != state.status:
            raise ConflictError(STALE_MESSAGE)

    @staticmethod
    def _summary(uow, dataset, identity, state, changes):
        # Dataset metadata is current configuration, not versioned content.
        metadata = ()
        counts = summary_counts(changes, uow.version_diff.counts(dataset.id, identity.baseline_id))
        return DiffSummary(identity, dataset.key, dataset.name, metadata, counts)

    def open_review(self, actor_id, dataset_id, expected=None):
        """One committed event before returning a new valid review; never a receipt claim."""
        try:
            with self.uow(write=True, serialized=True, translate_errors=True) as uow:
                dataset, identity, state, current, _old, changes = self._load(
                    uow, actor_id, dataset_id, expected, write=True
                )
                if state is not None and not uow.version_diff.validate_snapshot(state.version):
                    raise ValidationError("Invalid published snapshot")
                if not uow.version_diff.validate_concept(dataset_id, current):
                    raise ValidationError("Invalid concept Record content")
                result = self._summary(uow, dataset, identity, state, changes)
                if not result.has_changes:
                    raise ConflictError(
                        "No changes yet. Edit the concept before reviewing changes."
                    )
                self._calendar_check(state)
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action="dataset.version_diff_viewed",
                    details={
                        "baseline_version_number": identity.baseline_number,
                        "concept_revision": identity.revision,
                        **asdict(result.counts),
                    },
                )
            return result
        except (PersistenceConflictError, DatatypeError):
            raise ConflictError("Review could not be opened. Reload and try again.") from None

    def review(self, actor_id, identity, section="Summary", criteria=None):
        try:
            with self.uow(translate_errors=True) as uow:
                uow.begin_export_snapshot()
                dataset, actual, state, current, previous, changes = self._load(
                    uow, actor_id, identity.dataset_id, identity
                )
                criteria = DiffCriteria() if criteria is None else criteria
                self._criteria(criteria)
                if section == "Summary":
                    result = self._summary(uow, dataset, actual, state, changes)
                elif section == "Fields":
                    result = self._field_page(changes, criteria)
                elif section == "Records":
                    result = self._record_page(uow, identity, current, previous, criteria)
                else:
                    raise ValidationError("Choose Summary, Fields or Records.")
                self._calendar_check(state)
                return result
        except (PersistenceConflictError, DatatypeError):
            raise ConflictError("Review is unavailable. Reload and try again.") from None

    def field_detail(self, actor_id, identity, field_id):
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            _dataset, _actual, state, _new, _old, changes = self._load(
                uow, actor_id, identity.dataset_id, identity
            )
            result = next((item for item in changes if item.id == field_id), None)
            if result is None:
                raise NotFoundError("Field not found")
            self._calendar_check(state)
            return result

    def record_detail(self, actor_id, identity, record_id, page=1):
        try:
            with self.uow(translate_errors=True) as uow:
                uow.begin_export_snapshot()
                _dataset, _actual, state, current, previous, _changes = self._load(
                    uow, actor_id, identity.dataset_id, identity
                )
                if not _valid_identity(record_id):
                    raise NotFoundError("Record not found")
                if type(page) is not int or not 1 <= page <= 2_147_483_647:
                    raise ValidationError("Choose a valid detail page.")
                row = next(
                    iter(
                        uow.version_diff.records(
                            identity.dataset_id, identity.baseline_id, STATUSES, record_id
                        )
                    ),
                    None,
                )
                if row is None:
                    raise NotFoundError("Record not found")
                record = self._record(row, current, previous)
                values, total = uow.version_diff.values(
                    identity.dataset_id, identity.baseline_id, record_id, record.status, page
                )
                self._calendar_check(state)
                return RecordDetail(record, values, page, total)
        except (PersistenceConflictError, DatatypeError):
            raise ConflictError("Record review is unavailable. Reload and try again.") from None

    @staticmethod
    def _criteria(criteria):
        if (
            not isinstance(criteria, DiffCriteria)
            or not criteria.statuses
            or len(criteria.statuses) > 3
            or len(set(criteria.statuses)) != len(criteria.statuses)
            or any(status not in STATUSES for status in criteria.statuses)
            or not isinstance(criteria.search, str)
            or len(criteria.search) > MAX_SEARCH
            or (criteria.anchor and not _valid_identity(criteria.anchor))
            or type(criteria.backwards) is not bool
        ):
            raise ValidationError("Choose valid review filters.")

    @staticmethod
    def _field_page(changes, criteria):
        start = 0
        if criteria.anchor:
            index = next((i for i, item in enumerate(changes) if item.id == criteria.anchor), None)
            if index is None:
                raise NotFoundError("Field not found")
            start = max(0, index - PAGE_SIZE) if criteria.backwards else index + 1
        items = changes[start : start + PAGE_SIZE]
        return DiffPage(
            items,
            len(changes),
            items[0].id if start and items else "",
            items[-1].id if start + PAGE_SIZE < len(changes) and items else "",
            tuple(sum(item.status == status for item in changes) for status in STATUSES),
        )

    @staticmethod
    def _identifier(encoded, fields):
        if encoded is None:
            return None, ()
        if not isinstance(encoded, bytes) or not 1 <= len(encoded) <= 2000:
            raise ValidationError("Invalid stored Record identifier")
        offset, labels, ordering = 0, [], []
        for field in sorted((f for f in fields if f.is_identifier), key=lambda f: f.key):
            if offset + 4 > len(encoded):
                raise ValidationError("Invalid stored Record identifier")
            length = int.from_bytes(encoded[offset : offset + 4], "big")
            offset += 4
            key = encoded[offset : offset + length]
            offset += length
            datatype = get_datatype(field.data_type)
            if not datatype.identifier_eligible:
                raise ValidationError("Invalid stored Record identifier")
            value = datatype.from_key(key)
            labels.append(datatype.text(value))
            # Status groups have one schema each: Changed uses the new identifier.
            ordering.append(datatype.ordering_key(value))
        if not labels or offset != len(encoded):
            raise ValidationError("Invalid stored Record identifier")
        return " · ".join(labels), tuple(ordering)

    @classmethod
    def _record(cls, row, current, previous):
        if not _valid_identity(row.id):
            raise ValidationError("Invalid stored Record identity")
        old, old_sort = cls._identifier(row.before_identifier, previous)
        new, new_sort = cls._identifier(row.after_identifier, current)
        return RecordChange(
            row.id,
            row.status,
            old,
            new,
            row.changed_values,
            (STATUSES.index(row.status), new_sort if new is not None else old_sort, row.id),
        )

    def _record_page(self, uow, identity, current, previous, criteria):
        anchor = None
        if criteria.anchor:
            row = next(
                iter(
                    uow.version_diff.records(
                        identity.dataset_id,
                        identity.baseline_id,
                        criteria.statuses,
                        criteria.anchor,
                    )
                ),
                None,
            )
            if row is None:
                raise NotFoundError("Record not found")
            anchor = self._record(row, current, previous).sort_key
        total = 0
        status_counts = [0, 0, 0]
        search = normalize("NFC", criteria.search).casefold()

        def candidates():
            nonlocal total
            for row in uow.version_diff.records(
                identity.dataset_id, identity.baseline_id, criteria.statuses
            ):
                item = self._record(row, current, previous)
                if search and not any(
                    search in (label or "").casefold()
                    for label in (item.before_identifier, item.after_identifier)
                ):
                    continue
                total += 1
                status_counts[STATUSES.index(item.status)] += 1
                if anchor is None or (
                    item.sort_key < anchor if criteria.backwards else item.sort_key > anchor
                ):
                    yield item

        select_page = nlargest if criteria.backwards else nsmallest
        selected = select_page(PAGE_SIZE + 1, candidates(), key=lambda item: item.sort_key)
        has_more = len(selected) > PAGE_SIZE
        items = selected[:PAGE_SIZE]
        if criteria.backwards:
            items.reverse()
        return DiffPage(
            tuple(items),
            total,
            items[0].id
            if items and (has_more if criteria.backwards else anchor is not None)
            else "",
            items[-1].id
            if items and (anchor is not None if criteria.backwards else has_more)
            else "",
            tuple(status_counts),
        )
