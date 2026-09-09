"""Require Dataset owning teams and add orthogonal export access."""

import sqlalchemy as sa
from alembic import op

from refrncr.dataset_upgrade import require_resolved_ownership

revision = "0012_ref_73"
down_revision = "0011_ref_130"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Resolve every row before any DDL. The migration environment owns one
    # explicit transaction, including SQLite DDL/batch reconstruction.
    mapping = op.get_context().config.attributes.get("dataset_owner_mapping", {})
    resolved = require_resolved_ownership(connection, mapping)
    op.add_column("datasets", sa.Column("owner_team_id", sa.String(36), nullable=True))
    for dataset_id, team_id in resolved.items():
        connection.execute(
            sa.text("UPDATE datasets SET owner_team_id=:team WHERE id=:dataset"),
            {"team": team_id, "dataset": dataset_id},
        )
    with op.batch_alter_table("datasets") as batch:
        batch.alter_column("owner_team_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key(
            "fk_datasets_owner_team", "teams", ["owner_team_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_index("ix_datasets_owner_team_id", ["owner_team_id"])
    op.add_column("audit_events", sa.Column("details", sa.JSON(), nullable=True))
    op.create_table(
        "dataset_export_access",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("team_id", sa.String(36), sa.ForeignKey("teams.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND team_id IS NULL) OR "
            "(user_id IS NULL AND team_id IS NOT NULL)",
            name="ck_dataset_export_access_subject",
        ),
        sa.UniqueConstraint("dataset_id", "user_id", name="uq_dataset_export_access_user"),
        sa.UniqueConstraint("dataset_id", "team_id", name="uq_dataset_export_access_team"),
    )
    op.create_index("ix_dataset_export_access_user_id", "dataset_export_access", ["user_id"])
    op.create_index("ix_dataset_export_access_team_id", "dataset_export_access", ["team_id"])
    # Dataset grants remain inert historical rows, without any conversion. Grants
    # for every other resource type retain their existing behavior.


def downgrade():
    connection = op.get_bind()
    # Downgrading code would reactivate legacy roles and lose ownership/export
    # semantics. Require explicit operator cleanup, never silently broaden access.
    if connection.scalar(sa.text("SELECT count(*) FROM datasets")) or connection.scalar(
        sa.text("SELECT count(*) FROM grants WHERE resource_type='dataset'")
    ):
        raise RuntimeError(
            "Cannot downgrade REF-73 while Datasets or legacy Dataset grants exist; "
            "restore a pre-upgrade backup or explicitly remove them first"
        )
    op.drop_table("dataset_export_access")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_index("ix_datasets_owner_team_id")
        batch.drop_constraint("fk_datasets_owner_team", type_="foreignkey")
        batch.drop_column("owner_team_id")
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_column("details")
