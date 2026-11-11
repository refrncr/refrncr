"""Permit actor-free administrator password recovery events."""

from alembic import op

revision = "0036_ref_349"
down_revision = "0035_ref_294"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("ck_audit_events_actor", type_="check")
        batch.create_check_constraint(
            "ck_audit_events_actor",
            "actor_user_id IS NOT NULL OR "
            "(object_type = 'api_key' AND action = 'api_key.authentication_failed') OR "
            "object_type = 'security' OR "
            "(object_type = 'user' AND action = 'password_recovered')",
        )


def downgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql(
        "SELECT count(*) FROM audit_events WHERE actor_user_id IS NULL "
        "AND object_type = 'user' AND action = 'password_recovered'"
    ).scalar():
        raise RuntimeError("Password recovery audit history exists; restore a pre-upgrade backup")
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("ck_audit_events_actor", type_="check")
        batch.create_check_constraint(
            "ck_audit_events_actor",
            "actor_user_id IS NOT NULL OR "
            "(object_type = 'api_key' AND action = 'api_key.authentication_failed') OR "
            "object_type = 'security'",
        )
