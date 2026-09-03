"""Add stable Dataset business-key metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0007_ref_75"
down_revision = "0006_ref_74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_business_key_fields",
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("field_key", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id", "field_key"],
            ["dataset_fields.dataset_id", "dataset_fields.key"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dataset_id", "field_key"),
    )


def downgrade() -> None:
    # Operators must stop application writes before downgrading; this check is not
    # a concurrency-control mechanism.
    connection = op.get_bind()
    components = sa.table("dataset_business_key_fields", sa.column("dataset_id"))
    component_exists = connection.scalar(sa.select(sa.literal(1)).select_from(components).limit(1))
    if component_exists is not None:
        raise RuntimeError(
            "Cannot downgrade REF-75 while Dataset business-key rows exist; "
            "remove them explicitly first"
        )
    op.drop_table("dataset_business_key_fields")
