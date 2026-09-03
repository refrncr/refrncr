"""Add optimistic-concurrency revision to Dataset records."""

import sqlalchemy as sa
from alembic import op

revision = "0009_ref_80"
down_revision = "0008_ref_79"
branch_labels = None
depends_on = None

_VALUE_COLUMNS = (
    "record_id, field_id, dataset_id, string_value, number_value, boolean_value, "
    "date_value, canonical_value_key, unique_value_key"
)


def _backup_sqlite_values(connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            "CREATE TEMPORARY TABLE ref_80_record_values_backup AS "
            "SELECT * FROM dataset_record_values"
        )


def _restore_sqlite_values(connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            f"INSERT OR IGNORE INTO dataset_record_values ({_VALUE_COLUMNS}) "
            f"SELECT {_VALUE_COLUMNS} "
            "FROM ref_80_record_values_backup"
        )
        backup_count = connection.exec_driver_sql(
            "SELECT count(*) FROM ref_80_record_values_backup"
        ).scalar_one()
        restored_count = connection.exec_driver_sql(
            "SELECT count(*) FROM dataset_record_values"
        ).scalar_one()
        if restored_count != backup_count:
            raise RuntimeError("REF-80 migration could not preserve every Record value")
        connection.exec_driver_sql("DROP TABLE ref_80_record_values_backup")


def upgrade() -> None:
    connection = op.get_bind()
    with op.batch_alter_table("dataset_records") as batch:
        batch.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
    records = sa.table("dataset_records", sa.column("revision", sa.Integer()))
    op.execute(records.update().where(records.c.revision.is_(None)).values(revision=1))
    _backup_sqlite_values(connection)
    with op.batch_alter_table("dataset_records") as batch:
        batch.create_check_constraint("ck_dataset_records_revision_positive", "revision >= 1")
        batch.alter_column("revision", server_default=None)
    _restore_sqlite_values(connection)


def downgrade() -> None:
    connection = op.get_bind()
    _backup_sqlite_values(connection)
    with op.batch_alter_table("dataset_records") as batch:
        batch.drop_constraint("ck_dataset_records_revision_positive", type_="check")
        batch.drop_column("revision")
    _restore_sqlite_values(connection)
