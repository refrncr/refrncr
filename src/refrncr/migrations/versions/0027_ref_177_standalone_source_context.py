"""Allow editable source-context fields on standalone mappings."""

import sqlalchemy as sa
from alembic import op

revision = "0027_ref_177"
down_revision = "0026_ref_177"
branch_labels = None
depends_on = None


_LINKED_OR_STANDALONE_SOURCE = (
    "(mapping_role IN ('source_identifier', 'source_context') AND "
    "((source_field_id IS NULL AND source_field_key IS NULL) OR "
    "(source_field_id IS NOT NULL AND source_field_key IS NOT NULL))) OR "
    "(mapping_role IS NULL OR mapping_role = 'mapping_target') "
    "AND source_field_id IS NULL AND source_field_key IS NULL"
)

_LINKED_SOURCE_CONTEXT = (
    "(mapping_role = 'source_context' "
    "AND source_field_id IS NOT NULL AND source_field_key IS NOT NULL) OR "
    "(mapping_role = 'source_identifier' AND "
    "((source_field_id IS NULL AND source_field_key IS NULL) OR "
    "(source_field_id IS NOT NULL AND source_field_key IS NOT NULL))) OR "
    "(mapping_role IS NULL OR mapping_role = 'mapping_target') "
    "AND source_field_id IS NULL AND source_field_key IS NULL"
)


def upgrade():
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_constraint("ck_dataset_fields_mapping_source", type_="check")
        batch.create_check_constraint(
            "ck_dataset_fields_mapping_source", _LINKED_OR_STANDALONE_SOURCE
        )


def downgrade():
    standalone_context = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM dataset_fields WHERE mapping_role = 'source_context' "
                "AND source_field_id IS NULL LIMIT 1"
            )
        )
        .first()
    )
    if standalone_context:
        raise RuntimeError("Standalone source context fields exist; remove them before downgrading")
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_constraint("ck_dataset_fields_mapping_source", type_="check")
        batch.create_check_constraint("ck_dataset_fields_mapping_source", _LINKED_SOURCE_CONTEXT)
