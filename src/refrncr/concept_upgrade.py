"""Stopped-writer preflight and lossless conversion of implicit working copies."""

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from refrncr.clock import SYSTEM_CLOCK
from refrncr.concept_persistence import SqlAlchemyConceptOperations
from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.diff_classification import classify_fields
from refrncr.diff_persistence import SqlAlchemyDiffOperations
from refrncr.models import Dataset
from refrncr.persistence import (
    SqlAlchemyDatasetBusinessKeyOperations,
    SqlAlchemyDatasetFieldOperations,
)
from refrncr.services import ValidationError
from refrncr.version_contracts import SnapshotField
from refrncr.version_persistence import SqlAlchemyVersionOperations


class ConceptUpgradeError(RuntimeError):
    def __init__(self, invalid):
        self.invalid = tuple(invalid)
        super().__init__("Concept preflight failed for Dataset IDs: " + ", ".join(self.invalid))


def _decisions(connection):
    invalid, active = [], []
    with Session(bind=connection) as session:
        legacy_metadata = "definition" not in {
            c["name"] for c in inspect(connection).get_columns("dataset_fields")
        }
        versions = SqlAlchemyVersionOperations(session, legacy_metadata=legacy_metadata)
        diff = SqlAlchemyDiffOperations(
            session,
            legacy_schema="deleted_content"
            not in {c["name"] for c in inspect(connection).get_columns("dataset_records")},
        )
        # Select only columns that existed when migration 0016 was introduced. Using
        # the current Dataset mapper here makes historical upgrades depend on every
        # column added by later migrations.
        for dataset in session.execute(
            select(Dataset.id, Dataset.name, Dataset.description, Dataset.revision).order_by(
                Dataset.id
            )
        ):
            try:
                latest = versions.latest_non_cancelled(dataset.id)
                identifier = SqlAlchemyDatasetBusinessKeyOperations(session).get(dataset.id)
                current = tuple(
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
                        f.definition,
                        f.unit,
                    )
                    for f in SqlAlchemyDatasetFieldOperations(
                        session, legacy_metadata=legacy_metadata
                    ).list_for_dataset(dataset.id)
                )
                if not set(identifier.field_keys) <= {f.key for f in current} or any(
                    f.is_identifier
                    and (not f.is_required or not get_datatype(f.data_type).identifier_eligible)
                    for f in current
                ):
                    raise ValidationError("Invalid stored identifier configuration")
                previous = versions.fields(latest) if latest else ()
                changes = classify_fields(current, previous)
                if not diff.validate_concept(dataset.id, current) or (
                    latest
                    and (len(previous) != latest.field_count or not diff.validate_snapshot(latest))
                ):
                    raise ValidationError("Invalid stored content")
                if (
                    latest is None
                    or changes
                    or dataset.name != latest.name
                    or dataset.description != latest.description
                    or any(diff.counts(dataset.id, latest.id))
                ):
                    active.append((dataset.id, latest.id if latest else None, dataset.revision))
            except (ValidationError, DatatypeError, ValueError):
                invalid.append(dataset.id)
    return tuple(invalid), tuple(active)


def concept_preflight(connection):
    return _decisions(connection)[0]


def migrate_concepts(connection):
    invalid, active = _decisions(connection)
    if invalid:
        raise ConceptUpgradeError(invalid)
    now = SYSTEM_CLOCK.now()
    with Session(bind=connection) as session:
        concepts = SqlAlchemyConceptOperations(session, legacy_schema=True)
        for dataset_id, baseline_id, revision in active:
            concepts.create(dataset_id, baseline_id, None, now, revision)
        session.flush()
