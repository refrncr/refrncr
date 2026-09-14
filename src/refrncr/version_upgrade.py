"""REF-83 operator preflight; writers must be stopped for the complete upgrade."""

from types import SimpleNamespace

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from refrncr.dataset_versions import validated_snapshot
from refrncr.models import Dataset, utc_now
from refrncr.persistence import (
    SqlAlchemyDatasetBusinessKeyOperations,
    SqlAlchemyDatasetFieldOperations,
    SqlAlchemyDatasetRecordOperations,
)
from refrncr.services import ValidationError
from refrncr.version_contracts import APPLICATION_TIMEZONE, SummaryCounts
from refrncr.version_persistence import SqlAlchemyVersionOperations


class VersionUpgradeError(RuntimeError):
    """All invalid Dataset IDs, without content, driver details or local paths."""

    def __init__(self, invalid):
        self.invalid = tuple(invalid)
        super().__init__(
            "REF-83 publication preflight failed for Dataset IDs: "
            + ", ".join(self.invalid)
            + ". Correct their configuration/values with the prior application and retry."
        )


def _datasets(session):
    # Explicit old-schema projection: the revision column is added only after preflight.
    return tuple(
        SimpleNamespace(**row._mapping)
        for row in session.execute(
            select(
                Dataset.id,
                Dataset._key.label("key"),
                Dataset.name,
                Dataset.description,
            ).order_by(Dataset.id)
        )
    )


def _work(session):
    return SimpleNamespace(
        dataset_fields=SqlAlchemyDatasetFieldOperations(
            session,
            legacy_metadata="definition"
            not in {c["name"] for c in inspect(session.connection()).get_columns("dataset_fields")},
        ),
        dataset_business_keys=SqlAlchemyDatasetBusinessKeyOperations(session),
        dataset_records=SqlAlchemyDatasetRecordOperations(
            session,
            legacy_schema="deleted_content"
            not in {
                c["name"] for c in inspect(session.connection()).get_columns("dataset_records")
            },
        ),
    )


def publication_preflight(connection):
    """Validate all Datasets, with no snapshot or schema writes."""
    invalid = []
    with Session(bind=connection) as session:
        for dataset in _datasets(session):
            try:
                validated_snapshot(_work(session), dataset)
            except (ValidationError, ValueError):
                invalid.append(dataset.id)
    return tuple(invalid)


def create_initial_versions(connection):
    """Run only inside the migration's transaction after complete successful preflight."""
    now = utc_now().astimezone(APPLICATION_TIMEZONE)
    with Session(bind=connection) as session:
        versions = SqlAlchemyVersionOperations(
            session,
            legacy_metadata="definition"
            not in {c["name"] for c in inspect(connection).get_columns("dataset_version_fields")},
        )
        for dataset in _datasets(session):
            content = validated_snapshot(_work(session), dataset)
            summary = SummaryCounts(
                fields_added=len(content.fields), records_added=len(content.records)
            )
            versions.append(dataset.id, 1, now, None, now.date(), "", content, summary)
        session.flush()
    # No commit: the caller owns DDL, data, and migration revision atomically.
