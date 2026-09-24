"""Add mapping tables as a second Dataset kind."""

import sqlalchemy as sa
from alembic import op

revision = "0024_ref_177"
down_revision = "0023_ref_176"
branch_labels = None
depends_on = None


def upgrade():
    # Test and operator recovery paths can stamp an unversioned schema that was
    # already created from current metadata. Treat that complete shape as applied.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("datasets")}
    if "kind" in columns:
        return
    # Add the self-referencing columns before their constraints. Alembic cannot
    # topologically order new columns and self-FKs in one SQLite batch rebuild.
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=16), server_default="reference", nullable=False)
        )
        batch.add_column(sa.Column("mapping_mode", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("source_system", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("target_system", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("source_dataset_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("target_dataset_id", sa.String(length=36), nullable=True))
        batch.create_check_constraint("ck_datasets_kind", "kind IN ('reference', 'mapping')")
        batch.create_check_constraint(
            "ck_datasets_mapping_mode",
            "(kind = 'reference' AND mapping_mode IS NULL) OR "
            "(kind = 'mapping' AND mapping_mode IN ('standalone', 'linked'))",
        )
        batch.create_check_constraint(
            "ck_datasets_mapping_metadata",
            "kind = 'mapping' OR (source_system IS NULL AND target_system IS NULL "
            "AND source_dataset_id IS NULL AND target_dataset_id IS NULL)",
        )
    with op.batch_alter_table("datasets") as batch:
        batch.create_foreign_key(
            "fk_datasets_mapping_source",
            "datasets",
            ["source_dataset_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_datasets_mapping_target",
            "datasets",
            ["target_dataset_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_datasets_source_dataset_id", ["source_dataset_id"])
        batch.create_index("ix_datasets_target_dataset_id", ["target_dataset_id"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM datasets WHERE kind = 'mapping' LIMIT 1")).first():
        raise RuntimeError("Mapping tables exist; convert or remove them before downgrading")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_index("ix_datasets_target_dataset_id")
        batch.drop_index("ix_datasets_source_dataset_id")
        batch.drop_constraint("fk_datasets_mapping_target", type_="foreignkey")
        batch.drop_constraint("fk_datasets_mapping_source", type_="foreignkey")

    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_mapping_metadata", type_="check")
        batch.drop_constraint("ck_datasets_mapping_mode", type_="check")
        batch.drop_constraint("ck_datasets_kind", type_="check")
        batch.drop_column("target_dataset_id")
        batch.drop_column("source_dataset_id")
        batch.drop_column("target_system")
        batch.drop_column("source_system")
        batch.drop_column("mapping_mode")
        batch.drop_column("kind")
