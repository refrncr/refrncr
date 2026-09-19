"""User API keys and the narrowly approved unauthenticated security event."""

import sqlalchemy as sa
from alembic import op

revision = "0021_ref_40"
down_revision = "0020_ref_162"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("verifier", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 100", name="ck_api_keys_name"),
        sa.CheckConstraint("revision >= 1", name="ck_api_keys_revision"),
        sa.CheckConstraint("expires_at > created_at", name="ck_api_keys_expiry"),
    )
    op.create_index("ix_api_keys_owner_id", "api_keys", ["owner_id"])
    with op.batch_alter_table("audit_events") as batch:
        batch.alter_column("actor_user_id", existing_type=sa.String(36), nullable=True)
        batch.create_check_constraint(
            "ck_audit_events_actor",
            "actor_user_id IS NOT NULL OR "
            "(object_type = 'api_key' AND action = 'api_key.authentication_failed')",
        )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM api_keys")) or bind.scalar(
        sa.text("SELECT count(*) FROM audit_events WHERE actor_user_id IS NULL")
    ):
        raise RuntimeError(
            "API key or unauthenticated audit history exists; restore a pre-upgrade backup"
        )
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("ck_audit_events_actor", type_="check")
        batch.alter_column("actor_user_id", existing_type=sa.String(36), nullable=False)
    op.drop_table("api_keys")
