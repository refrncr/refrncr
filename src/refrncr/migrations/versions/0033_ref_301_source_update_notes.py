"""Retain context-field removal explanations for the lifetime of a mapping draft."""

import sqlalchemy as sa
from alembic import op

revision = "0033_ref_301"
down_revision = "0032_ref_239"
branch_labels = None
depends_on = None


def upgrade():
    if "source_update_notes" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("dataset_concepts")
    }:
        op.add_column(
            "dataset_concepts",
            sa.Column("source_update_notes", sa.JSON(none_as_null=True), nullable=True),
        )


def downgrade():
    with op.batch_alter_table("dataset_concepts") as batch:
        batch.drop_column("source_update_notes")
