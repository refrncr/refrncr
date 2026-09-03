"""Add normalized typed Dataset records."""

import sqlalchemy as sa
from alembic import op

revision = "0008_ref_79"
down_revision = "0007_ref_75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dataset_fields") as batch:
        batch.create_unique_constraint("uq_dataset_fields_id_dataset", ["id", "dataset_id"])
    op.create_table(
        "dataset_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("identifier_key", sa.LargeBinary(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "dataset_id", name="uq_dataset_records_id_dataset"),
        sa.UniqueConstraint(
            "dataset_id", "identifier_key", name="uq_dataset_records_dataset_identifier"
        ),
    )
    op.create_table(
        "dataset_record_values",
        sa.Column("record_id", sa.String(36), nullable=False),
        sa.Column("field_id", sa.String(36), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("string_value", sa.Text(), nullable=True),
        sa.Column("number_value", sa.Numeric(38, 18), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("date_value", sa.Date(), nullable=True),
        sa.Column("canonical_value_key", sa.LargeBinary(1010), nullable=False),
        sa.Column("unique_value_key", sa.LargeBinary(1010), nullable=True),
        sa.CheckConstraint(
            "(CASE WHEN string_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN number_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN boolean_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN date_value IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_dataset_record_values_exactly_one_typed_value",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "dataset_id"],
            ["dataset_fields.id", "dataset_fields.dataset_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["record_id", "dataset_id"],
            ["dataset_records.id", "dataset_records.dataset_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("record_id", "field_id"),
        sa.UniqueConstraint("record_id", "field_id", name="uq_dataset_record_values_record_field"),
        sa.UniqueConstraint(
            "dataset_id",
            "field_id",
            "unique_value_key",
            name="uq_dataset_record_values_unique_value",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    records = sa.table("dataset_records", sa.column("id"))
    if connection.scalar(sa.select(sa.literal(1)).select_from(records).limit(1)) is not None:
        raise RuntimeError(
            "Cannot downgrade REF-79 while Dataset record rows exist; remove them explicitly first"
        )
    op.drop_table("dataset_record_values")
    op.drop_table("dataset_records")
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_constraint("uq_dataset_fields_id_dataset", type_="unique")
