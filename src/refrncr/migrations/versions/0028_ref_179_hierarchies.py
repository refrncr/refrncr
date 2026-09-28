"""Standalone hierarchy structure, scoped fields and complete immutable snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0028_ref_179"
down_revision = "0027_ref_177"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "dataset_hierarchy_nodes" in sa.inspect(bind).get_table_names():
        return
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_kind", type_="check")
        batch.drop_constraint("ck_datasets_mapping_mode", type_="check")
        batch.create_check_constraint(
            "ck_datasets_kind", "kind IN ('reference', 'mapping', 'hierarchy')"
        )
        batch.create_check_constraint(
            "ck_datasets_mapping_mode",
            "(kind IN ('reference', 'hierarchy') AND mapping_mode IS NULL) OR "
            "(kind = 'mapping' AND mapping_mode IN ('standalone', 'linked'))",
        )
    for table, constraint in (
        ("dataset_fields", "ck_dataset_fields_hierarchy_scope"),
        ("dataset_version_fields", "ck_version_fields_hierarchy_scope"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("hierarchy_scope", sa.String(16), nullable=True))
            batch.create_check_constraint(
                constraint, "hierarchy_scope IS NULL OR hierarchy_scope IN ('leaves', 'all_nodes')"
            )
    op.create_table(
        "dataset_hierarchy_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("id", "dataset_id", name="uq_hierarchy_nodes_id_dataset"),
        sa.ForeignKeyConstraint(
            ["parent_id", "dataset_id"],
            ["dataset_hierarchy_nodes.id", "dataset_hierarchy_nodes.dataset_id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            name="fk_hierarchy_nodes_parent",
        ),
        sa.UniqueConstraint("dataset_id", "parent_id", "name", name="uq_hierarchy_nodes_sibling"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_hierarchy_nodes_name"),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_hierarchy_nodes_parent"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_hierarchy_nodes_revision"),
    )
    op.create_index(
        "uq_hierarchy_nodes_root",
        "dataset_hierarchy_nodes",
        ["dataset_id"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_table(
        "dataset_hierarchy_values",
        sa.Column("node_id", sa.String(36), primary_key=True),
        sa.Column("field_id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("canonical_key", sa.LargeBinary(1010), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["node_id", "dataset_id"],
            ["dataset_hierarchy_nodes.id", "dataset_hierarchy_nodes.dataset_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "dataset_id"],
            ["dataset_fields.id", "dataset_fields.dataset_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(canonical_key) BETWEEN 1 AND 1010", name="ck_hierarchy_values_key"
        ),
    )
    op.create_table(
        "dataset_version_hierarchy_nodes",
        sa.Column("version_id", sa.String(36), primary_key=True),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["version_id", "parent_id"],
            ["dataset_version_hierarchy_nodes.version_id", "dataset_version_hierarchy_nodes.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            name="fk_version_hierarchy_nodes_parent",
        ),
        sa.UniqueConstraint(
            "version_id", "parent_id", "name", name="uq_version_hierarchy_nodes_sibling"
        ),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 200", name="ck_version_hierarchy_nodes_name"
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_version_hierarchy_nodes_parent"
        ),
    )
    op.create_index(
        "uq_version_hierarchy_nodes_root",
        "dataset_version_hierarchy_nodes",
        ["version_id"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_table(
        "dataset_version_hierarchy_values",
        sa.Column("version_id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(36), primary_key=True),
        sa.Column("field_id", sa.String(36), primary_key=True),
        sa.Column("canonical_key", sa.LargeBinary(1010), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id", "node_id"],
            ["dataset_version_hierarchy_nodes.version_id", "dataset_version_hierarchy_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "field_id"],
            ["dataset_version_fields.version_id", "dataset_version_fields.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(canonical_key) BETWEEN 1 AND 1010", name="ck_version_hierarchy_values_key"
        ),
    )


def downgrade():
    bind = op.get_bind()
    if (
        bind.execute(sa.text("SELECT 1 FROM datasets WHERE kind = 'hierarchy' LIMIT 1")).first()
        or bind.execute(sa.text("SELECT 1 FROM dataset_hierarchy_nodes LIMIT 1")).first()
        or bind.execute(sa.text("SELECT 1 FROM dataset_version_hierarchy_nodes LIMIT 1")).first()
        or bind.execute(
            sa.text("SELECT 1 FROM dataset_fields WHERE hierarchy_scope IS NOT NULL LIMIT 1")
        ).first()
        or bind.execute(
            sa.text(
                "SELECT 1 FROM dataset_version_fields WHERE hierarchy_scope IS NOT NULL LIMIT 1"
            )
        ).first()
    ):
        raise RuntimeError("Hierarchy data exists; restore a pre-upgrade backup before downgrading")
    for table in (
        "dataset_version_hierarchy_values",
        "dataset_version_hierarchy_nodes",
        "dataset_hierarchy_values",
        "dataset_hierarchy_nodes",
    ):
        op.drop_table(table)
    for table, constraint in (
        ("dataset_version_fields", "ck_version_fields_hierarchy_scope"),
        ("dataset_fields", "ck_dataset_fields_hierarchy_scope"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="check")
            batch.drop_column("hierarchy_scope")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_kind", type_="check")
        batch.drop_constraint("ck_datasets_mapping_mode", type_="check")
        batch.create_check_constraint("ck_datasets_kind", "kind IN ('reference', 'mapping')")
        batch.create_check_constraint(
            "ck_datasets_mapping_mode",
            "(kind = 'reference' AND mapping_mode IS NULL) OR "
            "(kind = 'mapping' AND mapping_mode IN ('standalone', 'linked'))",
        )
