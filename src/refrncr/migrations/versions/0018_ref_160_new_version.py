"""Retain Deleted Record content and establish the new-import compatibility boundary."""

import sqlalchemy as sa
from alembic import op

revision = "0018_ref_160"
down_revision = "0017_ref_154"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    with op.batch_alter_table("dataset_concepts") as batch:
        batch.add_column(
            sa.Column("records_mutated", sa.Boolean(), nullable=True, server_default=sa.false())
        )
        batch.add_column(sa.Column("import_result", sa.JSON(none_as_null=True), nullable=True))
    # Explicit product decision: old audit cannot establish a clean Concept.
    connection.execute(sa.text("UPDATE dataset_concepts SET records_mutated = NULL"))
    with op.batch_alter_table("dataset_records") as batch:
        batch.add_column(sa.Column("deleted_content", sa.JSON(none_as_null=True), nullable=True))
        batch.drop_constraint("uq_dataset_records_dataset_identifier", type_="unique")
    op.create_index(
        "uq_dataset_records_dataset_identifier",
        "dataset_records",
        ["dataset_id", "identifier_key"],
        unique=True,
        sqlite_where=sa.text("deleted_content IS NULL"),
        postgresql_where=sa.text("deleted_content IS NULL"),
    )
    metadata = sa.MetaData()
    tables = {
        name: sa.Table(name, metadata, autoload_with=connection)
        for name in (
            "dataset_concepts",
            "datasets",
            "dataset_records",
            "dataset_version_records",
            "dataset_version_fields",
            "dataset_version_values",
        )
    }
    concepts, datasets, records, old_records, old_fields, old_values = tables.values()
    for concept in connection.execute(sa.select(concepts)).mappings():
        version_id = concept["base_version_id"]
        if version_id is None:
            continue
        fields = [
            {key: value for key, value in dict(row).items() if key != "version_id"}
            | {"dataset_id": concept["dataset_id"]}
            for row in connection.execute(
                sa.select(old_fields)
                .where(old_fields.c.version_id == version_id)
                .order_by(old_fields.c.display_position)
            ).mappings()
        ]
        missing = (
            connection.execute(
                sa.select(old_records).where(
                    old_records.c.version_id == version_id,
                    ~sa.exists(sa.select(records.c.id).where(records.c.id == old_records.c.id)),
                )
            )
            .mappings()
            .all()
        )
        dataset_revision = connection.scalar(
            sa.select(datasets.c.revision).where(datasets.c.id == concept["dataset_id"])
        )
        for record in missing:
            values = {
                row.field_id: row.canonical_key.hex()
                for row in connection.execute(
                    sa.select(old_values.c.field_id, old_values.c.canonical_key).where(
                        old_values.c.version_id == version_id,
                        old_values.c.record_id == record["id"],
                    )
                )
            }
            connection.execute(
                records.insert().values(
                    id=record["id"],
                    dataset_id=concept["dataset_id"],
                    identifier_key=record["identifier_key"],
                    revision=dataset_revision + 1,
                    created_at=concept["created_at"],
                    updated_at=concept["updated_at"],
                    deleted_content={"fields": fields, "values": values},
                )
            )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM dataset_records WHERE deleted_content IS NOT NULL")
    ):
        raise RuntimeError("Retained Deleted Records require a matching backup for downgrade")
    if connection.scalar(
        sa.text("SELECT count(*) FROM dataset_concepts WHERE import_result IS NOT NULL")
    ):
        raise RuntimeError("Finish imported Concepts before downgrade")
    op.drop_index("uq_dataset_records_dataset_identifier", table_name="dataset_records")
    with op.batch_alter_table("dataset_records") as batch:
        batch.drop_column("deleted_content")
        batch.create_unique_constraint(
            "uq_dataset_records_dataset_identifier", ["dataset_id", "identifier_key"]
        )
    with op.batch_alter_table("dataset_concepts") as batch:
        batch.drop_column("records_mutated")
        batch.drop_column("import_result")
