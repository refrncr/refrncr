"""Add mapping roles and source links to dataset fields."""

import sqlalchemy as sa
from alembic import op

revision = "0026_ref_177"
down_revision = "0025_ref_177"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("dataset_fields")}
    if "mapping_role" not in columns:
        with op.batch_alter_table("dataset_fields") as batch:
            batch.add_column(sa.Column("mapping_role", sa.String(length=24), nullable=True))
            batch.add_column(sa.Column("source_field_id", sa.String(length=36), nullable=True))
            batch.add_column(sa.Column("source_field_key", sa.String(length=100), nullable=True))
            batch.create_check_constraint(
                "ck_dataset_fields_mapping_role",
                "mapping_role IS NULL OR mapping_role IN "
                "('source_identifier', 'source_context', 'mapping_target')",
            )
            batch.create_check_constraint(
                "ck_dataset_fields_mapping_source",
                "(mapping_role = 'source_context' "
                "AND source_field_id IS NOT NULL AND source_field_key IS NOT NULL) OR "
                "(mapping_role = 'source_identifier' AND "
                "((source_field_id IS NULL AND source_field_key IS NULL) OR "
                "(source_field_id IS NOT NULL AND source_field_key IS NOT NULL))) OR "
                "(mapping_role IS NULL OR mapping_role = 'mapping_target') "
                "AND source_field_id IS NULL AND source_field_key IS NULL",
            )
        with op.batch_alter_table("dataset_fields") as batch:
            batch.create_foreign_key(
                "fk_dataset_fields_source",
                "dataset_fields",
                ["source_field_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch.create_index("ix_dataset_fields_source_field_id", ["source_field_id"])
    version_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("dataset_version_fields")
    }
    if "mapping_role" not in version_columns:
        with op.batch_alter_table("dataset_version_fields") as batch:
            batch.add_column(sa.Column("mapping_role", sa.String(length=24), nullable=True))
            batch.add_column(sa.Column("source_field_key", sa.String(length=100), nullable=True))
            batch.create_check_constraint(
                "ck_version_fields_mapping_role",
                "mapping_role IS NULL OR mapping_role IN "
                "('source_identifier', 'source_context', 'mapping_target')",
            )


def downgrade():
    bind = op.get_bind()
    populated = bind.execute(
        sa.text("SELECT 1 FROM dataset_fields WHERE mapping_role IS NOT NULL LIMIT 1")
    ).first()
    if populated:
        raise RuntimeError("Mapping field roles exist; remove them before downgrading")
    with op.batch_alter_table("dataset_version_fields") as batch:
        batch.drop_constraint("ck_version_fields_mapping_role", type_="check")
        batch.drop_column("source_field_key")
        batch.drop_column("mapping_role")
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_index("ix_dataset_fields_source_field_id")
        batch.drop_constraint("fk_dataset_fields_source", type_="foreignkey")
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_constraint("ck_dataset_fields_mapping_source", type_="check")
        batch.drop_constraint("ck_dataset_fields_mapping_role", type_="check")
        batch.drop_column("source_field_key")
        batch.drop_column("source_field_id")
        batch.drop_column("mapping_role")
