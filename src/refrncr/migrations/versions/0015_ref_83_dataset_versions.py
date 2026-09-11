"""REF-83 complete immutable versions with full publication preflight."""

import sqlalchemy as sa
from alembic import op

from refrncr.version_upgrade import (
    VersionUpgradeError,
    create_initial_versions,
    publication_preflight,
)

revision = "0015_ref_83"
down_revision = "0014_ref_145"
branch_labels = None
depends_on = None


def upgrade():
    invalid = publication_preflight(op.get_bind())
    if invalid:
        raise VersionUpgradeError(invalid)
    # New relational tables; historical revisions remain unchanged.
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publisher_id", sa.String(length=36), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("release_note", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("field_count", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("fields_added", sa.Integer(), nullable=False),
        sa.Column("fields_changed", sa.Integer(), nullable=False),
        sa.Column("fields_removed", sa.Integer(), nullable=False),
        sa.Column("records_added", sa.Integer(), nullable=False),
        sa.Column("records_changed", sa.Integer(), nullable=False),
        sa.Column("records_removed", sa.Integer(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelling_actor_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "(cancelled_at IS NULL AND cancelling_actor_id IS NULL) OR "
            "(cancelled_at IS NOT NULL AND cancelling_actor_id IS NOT NULL)",
            name="ck_dataset_versions_cancellation",
        ),
        sa.CheckConstraint("length(description) <= 10000", name="ck_dataset_versions_description"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_dataset_versions_name"),
        sa.CheckConstraint("length(release_note) <= 2000", name="ck_dataset_versions_note"),
        sa.CheckConstraint("number >= 1", name="ck_dataset_versions_number"),
        sa.ForeignKeyConstraint(["cancelling_actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publisher_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "number", name="uq_dataset_versions_number"),
    )
    op.create_table(
        "dataset_version_fields",
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("is_unique", sa.Boolean(), nullable=False),
        sa.Column("display_position", sa.Integer(), nullable=False),
        sa.Column("is_identifier", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "data_type IN ('string', 'integer', 'number', 'float', 'boolean', "
            "'date', 'time', 'datetime', 'timestamp')",
            name="ck_version_fields_type",
        ),
        sa.CheckConstraint("NOT is_identifier OR is_required", name="ck_version_fields_identifier"),
        sa.CheckConstraint("display_position >= 1", name="ck_version_fields_position"),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 100", name="ck_version_fields_key"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_version_fields_name"),
        sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("version_id", "id"),
        sa.UniqueConstraint("version_id", "display_position", name="uq_version_fields_position"),
        sa.UniqueConstraint("version_id", "key", name="uq_version_fields_key"),
    )
    op.create_table(
        "dataset_version_records",
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("identifier_key", sa.LargeBinary(length=2000), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("version_id", "id"),
        sa.UniqueConstraint("version_id", "identifier_key", name="uq_version_records_identifier"),
        sa.CheckConstraint(
            "length(identifier_key) BETWEEN 1 AND 2000", name="ck_version_records_key"
        ),
    )
    op.create_table(
        "dataset_version_values",
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=False),
        sa.Column("field_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_key", sa.LargeBinary(length=1010), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("unique_key", sa.LargeBinary(length=1010), nullable=True),
        sa.ForeignKeyConstraint(
            ["version_id", "field_id"],
            ["dataset_version_fields.version_id", "dataset_version_fields.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "record_id"],
            ["dataset_version_records.version_id", "dataset_version_records.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "record_id", "field_id"),
        sa.UniqueConstraint(
            "version_id", "field_id", "unique_key", name="uq_version_values_unique"
        ),
        sa.CheckConstraint(
            "length(canonical_key) BETWEEN 1 AND 1010", name="ck_version_values_key"
        ),
    )
    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision", sa.Integer(), server_default="1", nullable=False))

        batch_op.create_check_constraint("ck_datasets_revision", "revision >= 1")
    create_initial_versions(op.get_bind())


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM dataset_versions")):
        raise RuntimeError("Published Dataset versions exist; restore the pre-upgrade backup")
    op.drop_table("dataset_version_values")
    op.drop_table("dataset_version_records")
    op.drop_table("dataset_version_fields")
    op.drop_table("dataset_versions")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_revision", type_="check")
        batch.drop_column("revision")
