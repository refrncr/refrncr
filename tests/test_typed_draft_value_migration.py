"""REF-205 repairs corrupted typed draft values on SQLite and PostgreSQL."""

from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import select, text, update
from support import PASSWORD, config_for, configured_dataset

from refrncr.models import DatasetRecordValue


def _stored_values(factory, record_id):
    with factory() as session:
        return {
            row.field_id: row
            for row in session.scalars(
                select(DatasetRecordValue).where(DatasetRecordValue.record_id == record_id)
            )
        }


def test_upgrade_repairs_only_misplaced_typed_values_and_is_idempotent(audit_db):
    engine, factory, services = audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    dataset, fields = configured_dataset(services, admin)
    record = services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {"code": "keep", "amount": "12.50", "active": "false", "day": "2026-10-14"},
    )
    before = _stored_values(factory, record.id)
    keys = {
        field_id: (row.canonical_value_key, row.unique_value_key)
        for field_id, row in before.items()
    }

    with engine.begin() as connection:
        for field_key, canonical_text, typed_column in (
            ("amount", "12.5", "number_value"),
            ("active", "false", "boolean_value"),
            ("day", "2026-10-14", "date_value"),
        ):
            connection.execute(
                update(DatasetRecordValue)
                .where(DatasetRecordValue.field_id == fields[field_key].id)
                .values(string_value=canonical_text, **{typed_column: None})
            )

    with engine.connect() as connection:
        command.stamp(config_for(connection), "0029_ref_195")
        command.upgrade(config_for(connection), "head")
        connection.commit()

    repaired = _stored_values(factory, record.id)
    assert repaired[fields["code"].id].string_value == "keep"
    assert repaired[fields["amount"].id].number_value == Decimal("12.500000000000000000")
    assert repaired[fields["active"].id].boolean_value is False
    assert repaired[fields["day"].id].date_value == date(2026, 10, 14)
    assert all(repaired[fields[key].id].string_value is None for key in ("amount", "active", "day"))
    assert {
        field_id: (row.canonical_value_key, row.unique_value_key)
        for field_id, row in repaired.items()
    } == keys

    with engine.connect() as connection:
        command.downgrade(config_for(connection), "0029_ref_195")
        command.upgrade(config_for(connection), "head")
        connection.commit()
    repeated = _stored_values(factory, record.id)
    assert repeated[fields["amount"].id].number_value == Decimal("12.500000000000000000")
    assert repeated[fields["active"].id].boolean_value is False
    assert repeated[fields["day"].id].date_value == date(2026, 10, 14)


def test_upgrade_rejects_invalid_repair_candidate_atomically(audit_db):
    engine, _factory, services = audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    dataset, fields = configured_dataset(services, admin)
    record = services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {"code": "keep", "amount": "12.5", "active": "true", "day": "2026-10-14"},
    )
    with engine.begin() as connection:
        connection.execute(
            update(DatasetRecordValue)
            .where(DatasetRecordValue.field_id == fields["day"].id)
            .values(string_value="not-a-date", date_value=None)
        )

    with engine.connect() as connection:
        command.stamp(config_for(connection), "0029_ref_195")
        with pytest.raises(RuntimeError, match="REF-205"):
            command.upgrade(config_for(connection), "head")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0029_ref_195"
        stored = connection.execute(
            select(DatasetRecordValue.string_value, DatasetRecordValue.date_value).where(
                DatasetRecordValue.record_id == record.id,
                DatasetRecordValue.field_id == fields["day"].id,
            )
        ).one()
        assert stored == ("not-a-date", None)
