"""Add Community teams."""

import sqlalchemy as sa
from alembic import op

revision = "0002_ref_34"
down_revision = "0001_ref_33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("name_key", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 100", name="ck_teams_name_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key"),
    )


def downgrade() -> None:
    op.drop_table("teams")
