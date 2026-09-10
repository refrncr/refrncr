"""Index the existing audit chronology for bounded keyset reads."""

from alembic import op

revision = "0013_ref_39"
down_revision = "0012_ref_73"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_audit_events_timestamp_id", "audit_events", ["timestamp", "id"])


def downgrade():
    op.drop_index("ix_audit_events_timestamp_id", table_name="audit_events")
