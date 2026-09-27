"""Add selected fields for linked mapping tables."""

import sqlalchemy as sa
from alembic import op

revision = "0025_ref_177"
down_revision = "0024_ref_177"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("datasets")}
    if "source_field_key" in columns:
        return
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_mapping_metadata", type_="check")
        batch.add_column(sa.Column("source_field_key", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("target_field_key", sa.String(length=100), nullable=True))
        batch.create_check_constraint(
            "ck_datasets_mapping_metadata",
            "kind = 'mapping' OR (source_system IS NULL AND target_system IS NULL "
            "AND source_dataset_id IS NULL AND target_dataset_id IS NULL "
            "AND source_field_key IS NULL AND target_field_key IS NULL)",
        )


def downgrade():
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT 1 FROM datasets "
            "WHERE source_field_key IS NOT NULL OR target_field_key IS NOT NULL LIMIT 1"
        )
    ).first():
        raise RuntimeError("Linked mapping fields exist; clear them before downgrading")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_mapping_metadata", type_="check")
        batch.create_check_constraint(
            "ck_datasets_mapping_metadata",
            "kind = 'mapping' OR (source_system IS NULL AND target_system IS NULL "
            "AND source_dataset_id IS NULL AND target_dataset_id IS NULL)",
        )
        batch.drop_column("target_field_key")
        batch.drop_column("source_field_key")
