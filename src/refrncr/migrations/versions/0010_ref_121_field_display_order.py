"""Add explicit Dataset field display order."""

import sqlalchemy as sa
from alembic import op

revision = "0010_ref_121"
down_revision = "0009_ref_80"
branch_labels = None
depends_on = None


def _counts(connection):
    tables = (
        "datasets",
        "dataset_fields",
        "dataset_business_key_fields",
        "dataset_records",
        "dataset_record_values",
        "memberships",
        "grants",
        "audit_events",
    )
    return {
        table: connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
        for table in tables
    }


def _preserve_sqlite_dependents(connection):
    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql(
        "CREATE TEMPORARY TABLE ref_121_business_keys AS SELECT * FROM dataset_business_key_fields"
    )
    connection.exec_driver_sql(
        "CREATE TEMPORARY TABLE ref_121_record_values AS SELECT * FROM dataset_record_values"
    )


def _restore_sqlite_dependents(connection, expected):
    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO dataset_business_key_fields (dataset_id, field_key) "
        "SELECT dataset_id, field_key FROM ref_121_business_keys"
    )
    value_columns = (
        "record_id, field_id, dataset_id, string_value, number_value, boolean_value, "
        "date_value, canonical_value_key, unique_value_key"
    )
    connection.exec_driver_sql(
        f"INSERT OR IGNORE INTO dataset_record_values ({value_columns}) "
        f"SELECT {value_columns} FROM ref_121_record_values"
    )
    actual = _counts(connection)
    if actual != expected:
        raise RuntimeError("REF-121 migration could not preserve every dependent business row")
    connection.exec_driver_sql("DROP TABLE ref_121_business_keys")
    connection.exec_driver_sql("DROP TABLE ref_121_record_values")


def _finish_sqlite_reconstruction(connection) -> None:
    if connection.dialect.name == "sqlite":
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        if violations:
            raise RuntimeError("REF-121 migration found broken foreign-key relationships")


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        leftovers = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name = '_alembic_tmp_dataset_fields' "
            "UNION ALL SELECT name FROM sqlite_temp_master WHERE name IN "
            "('ref_121_business_keys', 'ref_121_record_values')"
        ).all()
        if leftovers:
            raise RuntimeError(
                "REF-121 found unfinished migration tables; restore the database backup"
            )
    expected = _counts(connection)
    field_columns = {
        column["name"]: column for column in sa.inspect(connection).get_columns("dataset_fields")
    }
    if "display_position" not in field_columns:
        op.add_column("dataset_fields", sa.Column("display_position", sa.Integer(), nullable=True))
    elif field_columns["display_position"]["nullable"] is not True:
        raise RuntimeError(
            "REF-121 found an unversioned display_position column in an unexpected state"
        )
    rows = connection.execute(
        sa.text("SELECT id, dataset_id FROM dataset_fields ORDER BY dataset_id, key, id")
    ).all()
    positions = {}
    for field_id, dataset_id in rows:
        positions[dataset_id] = positions.get(dataset_id, 0) + 1
        connection.execute(
            sa.text("UPDATE dataset_fields SET display_position=:position WHERE id=:id"),
            {"position": positions[dataset_id], "id": field_id},
        )
    updated = connection.exec_driver_sql(
        "SELECT count(*) FROM dataset_fields WHERE display_position IS NOT NULL"
    ).scalar_one()
    if updated != expected["dataset_fields"]:
        raise RuntimeError("REF-121 migration did not position every Dataset field")
    _preserve_sqlite_dependents(connection)
    try:
        with op.batch_alter_table("dataset_fields") as batch:
            batch.alter_column("display_position", existing_type=sa.Integer(), nullable=False)
            batch.create_check_constraint(
                "ck_dataset_fields_display_position_positive", "display_position >= 1"
            )
            batch.create_unique_constraint(
                "uq_dataset_fields_dataset_position", ["dataset_id", "display_position"]
            )
    finally:
        _finish_sqlite_reconstruction(connection)
    _restore_sqlite_dependents(connection, expected)
    if _counts(connection) != expected:
        raise RuntimeError("REF-121 migration changed business row counts")


def downgrade() -> None:
    connection = op.get_bind()
    expected = _counts(connection)
    _preserve_sqlite_dependents(connection)
    try:
        with op.batch_alter_table("dataset_fields") as batch:
            batch.drop_constraint("uq_dataset_fields_dataset_position", type_="unique")
            batch.drop_constraint("ck_dataset_fields_display_position_positive", type_="check")
            batch.drop_column("display_position")
    finally:
        _finish_sqlite_reconstruction(connection)
    _restore_sqlite_dependents(connection, expected)
    if _counts(connection) != expected:
        raise RuntimeError("REF-121 downgrade changed business row counts")
