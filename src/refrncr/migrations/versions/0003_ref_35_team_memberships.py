"""Add Community team memberships."""

import sqlalchemy as sa
from alembic import op

revision = "0003_ref_35"
down_revision = "0002_ref_34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "team_id", name="uq_memberships_user_team"),
    )
    op.create_index("ix_memberships_team_id", "memberships", ["team_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_index("ix_memberships_team_id", table_name="memberships")
    op.drop_table("memberships")
