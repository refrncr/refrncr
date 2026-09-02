"""Add typed Dataset field definitions."""

import sqlalchemy as sa
from alembic import op

revision = "0006_ref_74"
down_revision = "0005_ref_72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_fields",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_unique", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "data_type IN ('string', 'number', 'boolean', 'date')",
            name="ck_dataset_fields_data_type",
        ),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 100", name="ck_dataset_fields_key_length"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_dataset_fields_name_length"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "key", name="uq_dataset_fields_dataset_key"),
    )


def downgrade() -> None:
    # As with all destructive schema changes, operators must stop application writes
    # before downgrading; the empty-table check is not a concurrency-control mechanism.
    connection = op.get_bind()
    dataset_fields = sa.table("dataset_fields", sa.column("id"))
    field_exists = connection.scalar(sa.select(sa.literal(1)).select_from(dataset_fields).limit(1))
    if field_exists is not None:
        raise RuntimeError(
            "Cannot downgrade REF-74 while Dataset field rows exist; remove them explicitly first"
        )
    op.drop_table("dataset_fields")
