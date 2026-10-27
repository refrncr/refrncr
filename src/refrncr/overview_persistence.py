"""Batched attention projections and private navigation history."""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, func, select

from refrncr.models import (
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
    DatasetVersionField,
    DatasetVersionRecord,
    DatasetVersionValue,
    DatasetVisit,
)


class SqlAlchemyOverviewOperations:
    def __init__(self, session):
        self.session = session

    def recent_ids(self, user_id):
        return tuple(
            self.session.scalars(
                select(DatasetVisit.dataset_id)
                .where(DatasetVisit.user_id == user_id)
                .order_by(DatasetVisit.last_visited_at.desc(), DatasetVisit.dataset_id)
                .limit(20)
            )
        )

    def record_visit(self, user_id, dataset_id, now):
        # The service locks the user, serializing upserts and eviction across devices.
        latest = self.session.scalar(
            select(func.max(DatasetVisit.last_visited_at)).where(DatasetVisit.user_id == user_id)
        )
        # Preserve actual navigation order even for equal clock ticks or clock corrections.
        if latest is not None and now <= latest:
            now = latest + timedelta(microseconds=1)
        visit = self.session.get(DatasetVisit, (user_id, dataset_id))
        if visit is None:
            self.session.add(
                DatasetVisit(user_id=user_id, dataset_id=dataset_id, last_visited_at=now)
            )
        else:
            visit.last_visited_at = now
        self.session.flush()
        keep = self.recent_ids(user_id)
        self.session.execute(
            delete(DatasetVisit).where(
                DatasetVisit.user_id == user_id, DatasetVisit.dataset_id.not_in(keep)
            )
        )

    def version_fields(self, version_ids):
        result = defaultdict(list)
        if version_ids:
            for field in self.session.scalars(
                select(DatasetVersionField).where(DatasetVersionField.version_id.in_(version_ids))
            ):
                result[field.version_id].append(field)
        return result

    def draft_counts(self, dataset_ids):
        """Count missing targets and retained source removals without loading record values."""
        if not dataset_ids:
            return {}
        target = (
            select(DatasetRecordValue.record_id)
            .join(DatasetField, DatasetField.id == DatasetRecordValue.field_id)
            .where(
                DatasetRecordValue.record_id == DatasetRecord.id,
                DatasetField.mapping_role == "mapping_target",
            )
            .exists()
        )
        rows = self.session.execute(
            select(
                DatasetRecord.dataset_id,
                func.count().filter(DatasetRecord.deleted_content.is_(None), ~target),
                func.count().filter(DatasetRecord.deleted_content.is_not(None)),
            )
            .where(DatasetRecord.dataset_id.in_(dataset_ids))
            .group_by(DatasetRecord.dataset_id)
        )
        return {dataset_id: (unmapped, removed) for dataset_id, unmapped, removed in rows}

    def version_unmapped(self, version_ids):
        if not version_ids:
            return {}
        target = (
            select(DatasetVersionValue.record_id)
            .join(
                DatasetVersionField,
                (DatasetVersionField.id == DatasetVersionValue.field_id)
                & (DatasetVersionField.version_id == DatasetVersionValue.version_id),
            )
            .where(
                DatasetVersionValue.record_id == DatasetVersionRecord.id,
                DatasetVersionValue.version_id == DatasetVersionRecord.version_id,
                DatasetVersionField.mapping_role == "mapping_target",
            )
            .exists()
        )
        return dict(
            self.session.execute(
                select(DatasetVersionRecord.version_id, func.count())
                .where(DatasetVersionRecord.version_id.in_(version_ids), ~target)
                .group_by(DatasetVersionRecord.version_id)
            ).all()
        )
