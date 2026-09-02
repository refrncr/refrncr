"""Add protected Community datasets."""

import sqlalchemy as sa
from alembic import op

revision = "0005_ref_72"
down_revision = "0004_ref_36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(description) <= 10000", name="ck_datasets_description_length"),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 100", name="ck_datasets_key_length"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_datasets_name_length"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_datasets_key"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    datasets = sa.table("datasets", sa.column("id"))
    grants = sa.table("grants", sa.column("resource_type"))
    dataset_exists = connection.scalar(sa.select(sa.literal(1)).select_from(datasets).limit(1))
    dataset_grant_exists = connection.scalar(
        sa.select(sa.literal(1))
        .select_from(grants)
        .where(grants.c.resource_type == "dataset")
        .limit(1)
    )
    if dataset_exists is not None or dataset_grant_exists is not None:
        raise RuntimeError(
            "Cannot downgrade REF-72 while Dataset rows or Dataset grants exist; "
            "remove them explicitly first"
        )
    op.drop_table("datasets")
