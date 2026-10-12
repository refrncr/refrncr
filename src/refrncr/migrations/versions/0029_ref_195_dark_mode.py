"""Store the per-account theme preference."""

import sqlalchemy as sa
from alembic import op

revision = "0029_ref_195"
down_revision = "0028_ref_179"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "theme_preference" not in {
        column["name"] for column in sa.inspect(bind).get_columns("users")
    }:
        op.add_column(
            "users",
            sa.Column("theme_preference", sa.String(8), nullable=False, server_default="system"),
        )
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_users_theme_preference",
            "users",
            "theme_preference IN ('system', 'light', 'dark')",
        )


def downgrade():
    bind = op.get_bind()
    if "theme_preference" not in {
        column["name"] for column in sa.inspect(bind).get_columns("users")
    }:
        return
    if bind.dialect.name != "sqlite":
        op.drop_constraint("ck_users_theme_preference", "users", type_="check")
        op.drop_column("users", "theme_preference")
        return
    check_names = {
        constraint.get("name") for constraint in sa.inspect(bind).get_check_constraints("users")
    }
    with op.batch_alter_table("users") as batch:
        if "ck_users_theme_preference" in check_names:
            batch.drop_constraint("ck_users_theme_preference", type_="check")
        batch.drop_column("theme_preference")
