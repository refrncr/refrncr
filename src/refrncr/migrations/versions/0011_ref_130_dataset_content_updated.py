"""Track successful Dataset content/structure mutations independently of metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0011_ref_130"
down_revision = "0010_ref_121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "datasets", sa.Column("content_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE datasets SET content_updated_at = created_at")


def downgrade() -> None:
    # The migration environment suspends SQLite foreign keys during reconstruction
    # and validates them afterwards, preserving dependent rows on older SQLite too.
    with op.batch_alter_table("datasets") as batch:
        batch.drop_column("content_updated_at")
