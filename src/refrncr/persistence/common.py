from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from refrncr.clock import SYSTEM_CLOCK


def locked(statement, session: Session, for_update: bool):
    if for_update and session.get_bind().dialect.name != "sqlite":
        return statement.with_for_update().execution_options(populate_existing=True)
    return statement


def clock_now(session: Session):
    return session.info.get("clock", SYSTEM_CLOCK).now()


def safe_constraint_name(error: IntegrityError) -> str | None:
    """Return only known canonical constraint identifiers, never driver detail."""
    diagnostic = getattr(error.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "uq_datasets_key":
        return "uq_datasets_key"
    if getattr(diagnostic, "constraint_name", None) == "uq_dataset_fields_dataset_key":
        return "uq_dataset_fields_dataset_key"
    if getattr(diagnostic, "constraint_name", None) == "uq_dataset_fields_dataset_position":
        return "uq_dataset_fields_dataset_position"
    if getattr(diagnostic, "constraint_name", None) in {
        "uq_dataset_export_access_user",
        "uq_dataset_export_access_team",
        "uq_dataset_records_dataset_identifier",
        "uq_dataset_record_values_unique_value",
    }:
        return diagnostic.constraint_name  # type: ignore[union-attr]
    if str(error.orig) == "UNIQUE constraint failed: datasets.key":
        return "uq_datasets_key"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_fields.dataset_id, dataset_fields.key"
    ):
        return "uq_dataset_fields_dataset_key"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_fields.dataset_id, dataset_fields.display_position"
    ):
        return "uq_dataset_fields_dataset_position"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_records.dataset_id, dataset_records.identifier_key"
    ):
        return "uq_dataset_records_dataset_identifier"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_record_values.dataset_id, "
        "dataset_record_values.field_id, dataset_record_values.unique_value_key"
    ):
        return "uq_dataset_record_values_unique_value"
    for subject in ("user", "team"):
        if str(error.orig) == (
            "UNIQUE constraint failed: dataset_export_access.dataset_id, "
            f"dataset_export_access.{subject}_id"
        ):
            return f"uq_dataset_export_access_{subject}"
    return None
