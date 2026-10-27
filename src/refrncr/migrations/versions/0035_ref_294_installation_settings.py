"""Install the Community-wide export policy, defaulting existing installs to Everyone."""

import sqlalchemy as sa
from alembic import op

revision = "0035_ref_294"
down_revision = "0034_ref_238"
branch_labels = None
depends_on = None


def upgrade():
    if "installation_settings" in sa.inspect(op.get_bind()).get_table_names():
        return
    table = op.create_table(
        "installation_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("export_access_policy", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "id = '00000000-0000-0000-0000-000000000001'",
            name="ck_installation_settings_singleton",
        ),
        sa.CheckConstraint(
            "export_access_policy IN ('everyone', 'team_grants', 'admin_grants')",
            name="ck_installation_settings_export_access_policy",
        ),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "export_access_policy": "everyone",
            }
        ],
    )


def downgrade():
    op.drop_table("installation_settings")
