"""Allow the full pinned Lucide catalogue, including names longer than 32 characters."""

import sqlalchemy as sa
from alembic import op

revision = "0020_ref_162"
down_revision = "0019_ref_162"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("datasets") as batch:
        batch.alter_column("icon", existing_type=sa.String(32), type_=sa.String(64))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM datasets WHERE length(icon) > 32")):
        raise RuntimeError(
            "Icon names exceed 32 characters; shorten or clear them before downgrade"
        )
    with op.batch_alter_table("datasets") as batch:
        batch.alter_column("icon", existing_type=sa.String(64), type_=sa.String(32))
