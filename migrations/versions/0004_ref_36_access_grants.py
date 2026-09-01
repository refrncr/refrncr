"""Add Community access grants."""

import sqlalchemy as sa
from alembic import op

revision = "0004_ref_36"
down_revision = "0003_ref_35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("permission", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("subject_type IN ('user', 'team')", name="ck_grants_subject_type"),
        sa.CheckConstraint(
            "permission IN ('viewer', 'editor', 'admin')", name="ck_grants_permission"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subject_type",
            "subject_id",
            "resource_type",
            "resource_id",
            name="uq_grants_subject_resource",
        ),
    )
    op.create_index("ix_grants_subject_type", "grants", ["subject_type"])
    op.create_index("ix_grants_subject_id", "grants", ["subject_id"])
    op.create_index("ix_grants_resource_type", "grants", ["resource_type"])
    op.create_index("ix_grants_resource_id", "grants", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_grants_resource_id", table_name="grants")
    op.drop_index("ix_grants_resource_type", table_name="grants")
    op.drop_index("ix_grants_subject_id", table_name="grants")
    op.drop_index("ix_grants_subject_type", table_name="grants")
    op.drop_table("grants")
