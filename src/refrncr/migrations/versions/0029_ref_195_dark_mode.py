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
    check_names = {
        constraint.get("name") for constraint in sa.inspect(bind).get_check_constraints("users")
    }
    if bind.dialect.name != "sqlite" and "ck_users_theme_preference" not in check_names:
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
    check_names = {
        constraint.get("name") for constraint in sa.inspect(bind).get_check_constraints("users")
    }
    if bind.dialect.name != "sqlite":
        if "ck_users_theme_preference" in check_names:
            op.drop_constraint("ck_users_theme_preference", "users", type_="check")
        op.drop_column("users", "theme_preference")
        return
    with op.batch_alter_table("users") as batch:
        if "ck_users_theme_preference" in check_names:
            batch.drop_constraint("ck_users_theme_preference", type_="check")
        batch.drop_column("theme_preference")
