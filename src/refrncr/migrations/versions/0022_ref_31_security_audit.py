"""Permit anonymous security outcomes in the existing audit trail."""

from alembic import op

revision = "0022_ref_31"
down_revision = "0021_ref_40"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("ck_audit_events_actor", type_="check")
        batch.create_check_constraint(
            "ck_audit_events_actor",
            "actor_user_id IS NOT NULL OR "
            "(object_type = 'api_key' AND action = 'api_key.authentication_failed') OR "
            "object_type = 'security'",
        )


def downgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql(
        "SELECT count(*) FROM audit_events WHERE actor_user_id IS NULL AND object_type = 'security'"
    ).scalar():
        raise RuntimeError("Anonymous security audit history exists; restore a pre-upgrade backup")
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("ck_audit_events_actor", type_="check")
        batch.create_check_constraint(
            "ck_audit_events_actor",
            "actor_user_id IS NOT NULL OR "
            "(object_type = 'api_key' AND action = 'api_key.authentication_failed')",
        )
