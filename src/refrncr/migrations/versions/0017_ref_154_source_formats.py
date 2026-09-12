"""REF-154 remembered source-format proposals, without Record/Field rewrites."""

import sqlalchemy as sa
from alembic import op

revision = "0017_ref_154"
down_revision = "0016_ref_84"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dataset_field_source_formats",
        sa.Column("field_id", sa.String(36), nullable=False),
        sa.Column("choice", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("field_id"),
        sa.ForeignKeyConstraint(["field_id"], ["dataset_fields.id"], ondelete="CASCADE"),
    )


def downgrade():
    # Only changeable import suggestions are lost; no canonical Records or audit.
    op.drop_table("dataset_field_source_formats")
