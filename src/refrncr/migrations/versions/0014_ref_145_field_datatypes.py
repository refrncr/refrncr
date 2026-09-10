"""Extend Field type IDs without rewriting existing Record values or keys."""

import math

import sqlalchemy as sa
from alembic import op

from refrncr.datatypes import DatatypeError, get_datatype

revision = "0014_ref_145"
down_revision = "0013_ref_39"
branch_labels = None
depends_on = None

_OLD = ("string", "number", "boolean", "date")
_NEW = (*_OLD, "integer", "float", "time", "datetime", "timestamp")


def _preflight(connection, allowed):
    """Check historical state before DDL; emit no contents, identifiers or events."""
    invalid_type = connection.scalar(
        sa.text("SELECT count(*) FROM dataset_fields WHERE data_type NOT IN :allowed").bindparams(
            sa.bindparam("allowed", expanding=True)
        ),
        {"allowed": allowed},
    )
    if invalid_type:
        raise RuntimeError(
            "REF-145 found unsupported Field types; restore or repair before upgrade"
        )
    rows = connection.execute(
        sa.text(
            "SELECT f.data_type, f.is_unique, v.string_value, v.number_value, "
            "v.boolean_value, v.date_value, v.canonical_value_key, v.unique_value_key "
            "FROM dataset_record_values v JOIN dataset_fields f "
            "ON f.id=v.field_id AND f.dataset_id=v.dataset_id"
        ).execution_options(yield_per=1000)
    ).mappings()
    try:
        for row in rows:
            datatype = get_datatype(row["data_type"])
            raw_key = row["canonical_value_key"]
            if not isinstance(raw_key, (bytes, memoryview)) or len(raw_key) > 1010:
                raise ValueError
            key = bytes(raw_key)
            value = datatype.from_key(key)
            columns = {name: row[name + "_value"] for name in _OLD}
            column = datatype.id if datatype.id in _OLD else "string"
            if sum(item is not None for item in columns.values()) != 1 or columns[column] is None:
                raise ValueError
            if datatype.id == "string" and columns[column] != value:
                raise ValueError
            if datatype.id == "number" and not math.isfinite(columns[column]):
                raise ValueError
            if datatype.id == "boolean" and columns[column] not in (0, 1, False, True):
                raise ValueError
            if datatype.id == "boolean" and bool(columns[column]) != value:
                raise ValueError
            if datatype.id == "date" and str(columns[column]) != value.isoformat():
                raise ValueError
            if datatype.id not in _OLD and columns[column] != datatype.text(value):
                raise ValueError
            unique = row["unique_value_key"]
            if unique is not None and (
                not isinstance(unique, (bytes, memoryview)) or len(unique) > 1010
            ):
                raise ValueError
            if (bytes(unique) if unique is not None else None) != (
                key if row["is_unique"] else None
            ):
                raise ValueError
    except (ValueError, TypeError, DatatypeError):
        raise RuntimeError(
            "REF-145 found invalid historical Record values; restore or repair before upgrade"
        ) from None
    finally:
        rows.close()
    invalid_identifier = connection.scalar(
        sa.text(
            "SELECT count(*) FROM dataset_business_key_fields b JOIN dataset_fields f "
            "ON b.dataset_id=f.dataset_id AND b.field_key=f.key "
            "WHERE f.data_type='float' OR NOT f.is_required"
        )
    )
    if invalid_identifier:
        raise RuntimeError("REF-145 found an invalid Record identifier definition")


def _constraint(types):
    return "data_type IN (" + ", ".join(repr(value) for value in types) + ")"


def upgrade():
    connection = op.get_bind()
    _preflight(connection, _OLD)
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_constraint("ck_dataset_fields_data_type", type_="check")
        batch.create_check_constraint("ck_dataset_fields_data_type", _constraint(_NEW))


def downgrade():
    connection = op.get_bind()
    _preflight(connection, _OLD)
    with op.batch_alter_table("dataset_fields") as batch:
        batch.drop_constraint("ck_dataset_fields_data_type", type_="check")
        batch.create_check_constraint("ck_dataset_fields_data_type", _constraint(_OLD))
