"""Keep required mapping source-version selection in the service policy."""

from alembic import op

revision = "0032_ref_239"
down_revision = "0031_ref_239"
branch_labels = None
depends_on = None


def _constraint(require_version):
    condition = (
        "kind <> 'mapping' OR (mapping_mode = 'linked' AND source_dataset_id IS NOT NULL "
        "AND source_field_key IS NOT NULL"
    )
    if require_version:
        condition += " AND source_version_id IS NOT NULL"
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_mapping_source", type_="check")
        batch.create_check_constraint("ck_datasets_mapping_source", condition + ")")


def upgrade():
    _constraint(False)


def downgrade():
    _constraint(True)
