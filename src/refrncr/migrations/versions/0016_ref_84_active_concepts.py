"""REF-84 explicit active concepts; preserve all divergent working content."""

import sqlalchemy as sa
from alembic import op

from refrncr.concept_upgrade import ConceptUpgradeError, concept_preflight, migrate_concepts

revision = "0016_ref_84"
down_revision = "0015_ref_83"
branch_labels = None
depends_on = None


def upgrade():
    invalid = concept_preflight(op.get_bind())
    if invalid:
        raise ConceptUpgradeError(invalid)
    # An index provides the composite FK target without reconstructing snapshot tables.
    op.create_index(
        "uq_dataset_versions_dataset_id", "dataset_versions", ["dataset_id", "id"], unique=True
    )
    op.create_table(
        "dataset_concepts",
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("base_version_id", sa.String(36), nullable=True),
        sa.Column("creator_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("dataset_id"),
        sa.UniqueConstraint("id", name="uq_dataset_concepts_id"),
        sa.CheckConstraint("start_revision >= 1", name="ck_dataset_concepts_revision"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["dataset_id", "base_version_id"],
            ["dataset_versions.dataset_id", "dataset_versions.id"],
            ondelete="CASCADE",
            name="fk_concepts_baseline",
        ),
    )
    migrate_concepts(op.get_bind())


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM dataset_concepts")):
        raise RuntimeError("Active concepts exist; restore the pre-upgrade backup")
    op.drop_table("dataset_concepts")
    op.drop_index("uq_dataset_versions_dataset_id", table_name="dataset_versions")
