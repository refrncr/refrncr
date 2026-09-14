"""Optional Dataset icon and versioned descriptive Field metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0019_ref_162"
down_revision = "0018_ref_160"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("datasets", sa.Column("icon", sa.String(32), nullable=True))
    for table, prefix in (
        ("dataset_fields", "dataset_fields"),
        ("dataset_version_fields", "version_fields"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("definition", sa.String(4000), nullable=True))
            batch.add_column(sa.Column("unit", sa.String(64), nullable=True))
            batch.create_check_constraint(f"ck_{prefix}_definition", "length(definition) <= 4000")
            batch.create_check_constraint(f"ck_{prefix}_unit", "length(unit) <= 64")
    # No content backfill. Legacy retained-deletion Field payloads also deserialize
    # with SnapshotField's null defaults, without rewriting those historical payloads.


def downgrade():
    for table, prefix in (
        ("dataset_version_fields", "version_fields"),
        ("dataset_fields", "dataset_fields"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{prefix}_unit", type_="check")
            batch.drop_constraint(f"ck_{prefix}_definition", type_="check")
            batch.drop_column("unit")
            batch.drop_column("definition")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_column("icon")
