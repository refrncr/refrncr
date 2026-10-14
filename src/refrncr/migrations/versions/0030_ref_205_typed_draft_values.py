"""Repair typed draft values written into the string column."""

import sqlalchemy as sa
from alembic import op

from refrncr.datatypes import DatatypeError, get_datatype

revision = "0030_ref_205"
down_revision = "0029_ref_195"
branch_labels = None
depends_on = None


def _field_type(values, fields, data_type):
    return sa.exists(
        sa.select(1).where(
            fields.c.id == values.c.field_id,
            fields.c.dataset_id == values.c.dataset_id,
            fields.c.data_type == data_type,
        )
    )


def _repair(connection):
    metadata = sa.MetaData()
    values = sa.Table("dataset_record_values", metadata, autoload_with=connection)
    fields = sa.Table("dataset_fields", metadata, autoload_with=connection)

    candidates = connection.execute(
        sa.select(
            fields.c.data_type,
            values.c.string_value,
            values.c.canonical_value_key,
        )
        .join(
            fields,
            (fields.c.id == values.c.field_id) & (fields.c.dataset_id == values.c.dataset_id),
        )
        .where(
            values.c.string_value.is_not(None),
            fields.c.data_type.in_(("number", "boolean", "date")),
        )
        .execution_options(yield_per=1000)
    )
    try:
        for data_type, canonical_text, canonical_key in candidates:
            parsed = get_datatype(data_type).canonicalize(canonical_text)
            if parsed is None or parsed[1] != bytes(canonical_key):
                raise ValueError
    except (DatatypeError, TypeError, ValueError):
        raise RuntimeError(
            "REF-205 found an invalid typed draft value; restore or repair before upgrade"
        ) from None
    finally:
        candidates.close()

    connection.execute(
        sa.update(values)
        .where(values.c.string_value.is_not(None), _field_type(values, fields, "number"))
        .values(
            string_value=None,
            number_value=sa.cast(values.c.string_value, sa.Numeric(38, 18)),
        )
    )
    connection.execute(
        sa.update(values)
        .where(values.c.string_value.is_not(None), _field_type(values, fields, "boolean"))
        .values(
            string_value=None,
            boolean_value=sa.case(
                (values.c.string_value == "true", sa.true()),
                else_=sa.false(),
            ),
        )
    )
    date_value = values.c.string_value
    if connection.dialect.name != "sqlite":
        date_value = sa.cast(values.c.string_value, sa.Date())
    connection.execute(
        sa.update(values)
        .where(values.c.string_value.is_not(None), _field_type(values, fields, "date"))
        .values(string_value=None, date_value=date_value)
    )


def upgrade():
    _repair(op.get_bind())


def downgrade():
    # The upgrade restores the invariant already required by the preceding schema.
    # Reintroducing corrupt storage on downgrade would make drafts unreadable again.
    pass
