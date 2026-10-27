"""Add private dataset visits without backfilling browsing history."""

import sqlalchemy as sa
from alembic import op

revision = "0034_ref_238"
down_revision = "0033_ref_301"
branch_labels = None
depends_on = None


def upgrade():
    # Existing legacy-baseline upgrades can already contain tables created from metadata.
    if "dataset_visits" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "dataset_visits",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_visited_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dataset_visits_recent", "dataset_visits", ["user_id", "last_visited_at"])


def downgrade():
    op.drop_table("dataset_visits")
