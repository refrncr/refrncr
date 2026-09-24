"""Enforce unambiguous effective dates for published Dataset versions."""

from alembic import op
from sqlalchemy import inspect, text

revision = "0023_ref_176"
down_revision = "0022_ref_31"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    ambiguous = bind.execute(
        text(
            "SELECT dataset_id, effective_date FROM dataset_versions "
            "WHERE cancelled_at IS NULL GROUP BY dataset_id, effective_date HAVING count(*) > 1"
        )
    ).first()
    if ambiguous is not None:
        raise RuntimeError(
            "Ambiguous effective dates exist; resolve duplicate published dates before upgrading"
        )
    existing = {index["name"] for index in inspect(bind).get_indexes("dataset_versions")}
    if "uq_dataset_versions_effective_date" not in existing:
        op.create_index(
            "uq_dataset_versions_effective_date",
            "dataset_versions",
            ["dataset_id", "effective_date"],
            unique=True,
            sqlite_where=text("cancelled_at IS NULL"),
            postgresql_where=text("cancelled_at IS NULL"),
        )


def downgrade():
    op.drop_index("uq_dataset_versions_effective_date", table_name="dataset_versions")
