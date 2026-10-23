"""Pin mapping working content and publications to a published source version."""

import sqlalchemy as sa
from alembic import op

revision = "0031_ref_239"
down_revision = "0030_ref_205"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "source_version_id" in {
        column["name"] for column in sa.inspect(bind).get_columns("datasets")
    }:
        return
    if bind.scalar(sa.text("SELECT count(*) FROM datasets WHERE kind = 'mapping'")):
        raise RuntimeError(
            "REF-239 requires existing mapping tables to be deleted manually before upgrade. "
            "No mapping data has been migrated or deleted."
        )
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_constraint("fk_dataset_fields_source", type_="foreignkey")
    with op.batch_alter_table("dataset_versions") as batch:
        batch.add_column(sa.Column("source_version_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_versions_source_version",
            "dataset_versions",
            ["source_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(sa.Column("source_version_id", sa.String(36), nullable=True))
        batch.create_check_constraint(
            "ck_datasets_mapping_source",
            "kind <> 'mapping' OR (mapping_mode = 'linked' AND source_dataset_id IS NOT NULL "
            "AND source_field_key IS NOT NULL AND source_version_id IS NOT NULL)",
        )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM datasets WHERE kind = 'mapping'")):
        raise RuntimeError("Delete mapping tables before downgrading their version contract.")
    with op.batch_alter_table("dataset_fields") as batch:
        batch.create_foreign_key(
            "fk_dataset_fields_source",
            "dataset_fields",
            ["source_field_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_mapping_source", type_="check")
        batch.drop_column("source_version_id")
    with op.batch_alter_table("dataset_versions") as batch:
        batch.drop_constraint("fk_versions_source_version", type_="foreignkey")
        batch.drop_column("source_version_id")
