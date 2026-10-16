"""Independent REF-145 adversarial adapter, concurrency and rollback review."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session
from support import FIELDS, PASSWORD, setup_dataset, stored

from refrncr import dataset_export
from refrncr.dataset_export import ExportGenerationError, generate_export
from refrncr.datatypes import DATATYPES, canonicalize, get_datatype
from refrncr.db.base import Base
from refrncr.models import AuditEvent, DatasetField, DatasetRecord, DatasetRecordValue
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import RecordConflictError, StaleRecordError, ValidationError


def _build_reviewed(audit_db):
    engine, factory, services = audit_db
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    return engine, factory, services, admin


@pytest.fixture
def reviewed(audit_db):
    return _build_reviewed(audit_db)


@pytest.fixture
def database_reviewed(database_audit_db):
    return _build_reviewed(database_audit_db)


def state(engine):
    with engine.connect() as connection:
        return {
            table.name: sorted(connection.execute(select(table)).all(), key=repr)
            for table in Base.metadata.sorted_tables
        }


@pytest.mark.parametrize("stage", ["before_mutation", "before_audit", "audit_insert", "commit"])
@pytest.mark.parametrize(
    "operation", ["field_create", "field_update", "record_create", "record_update"]
)
def test_all_datatype_mutations_are_atomic_at_each_failure_stage(
    database_reviewed, monkeypatch, stage, operation
):
    engine, _factory, services, admin = database_reviewed
    dataset, _fields = setup_dataset(services, admin)
    record = services.dataset_records.create_record(admin.id, dataset.key, {"id": "one"})
    before = state(engine)
    reached = []

    def fail_sql(_conn, _cursor, statement, _parameters, _context, _many):
        sql = statement.lstrip().upper()
        target = "DATASET_FIELDS" if operation.startswith("field") else "DATASET_RECORD"
        if (
            stage == "before_mutation"
            and sql.startswith(("INSERT", "UPDATE", "DELETE"))
            and target in sql
        ):
            reached.append(stage)
            raise RuntimeError("injected failure")
        if stage == "audit_insert" and sql.startswith("INSERT INTO AUDIT_EVENTS"):
            reached.append(stage)
            raise RuntimeError("injected failure")

    def fail_audit(self, **_values):
        self.flush()
        reached.append(stage)
        raise RuntimeError("injected failure")

    def fail_commit(self):
        self.flush()
        reached.append(stage)
        raise RuntimeError("injected failure")

    event.listen(engine, "before_cursor_execute", fail_sql)
    try:
        with monkeypatch.context() as patch:
            if stage == "before_audit":
                patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
            if stage == "commit":
                patch.setattr(Session, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="injected failure"):
                if operation == "field_create":
                    services.dataset_fields.create_field(
                        admin.id, dataset.key, "extra", "Extra", "float"
                    )
                elif operation == "field_update":
                    services.dataset_fields.update_field(
                        admin.id, dataset.key, "timestamp", "Changed", "datetime", False, True
                    )
                elif operation == "record_create":
                    services.dataset_records.create_record(
                        admin.id,
                        dataset.key,
                        {"id": "two", "timestamp": "2024-01-01T01:00:00+01:00"},
                    )
                else:
                    services.dataset_records.update_record(
                        admin.id,
                        dataset.key,
                        record.id,
                        1,
                        {"id": "one", "integer": "9007199254740993"},
                    )
    finally:
        event.remove(engine, "before_cursor_execute", fail_sql)
    assert reached == [stage]
    assert state(engine) == before


@pytest.mark.parametrize(
    "type_id,variants",
    [
        ("integer", ("0009007199254740993", "9007199254740993")),
        ("number", ("0.100000000000000001", "0.100000000000000001")),
        ("timestamp", ("2024-01-01T01:00:00+01:00", "2024-01-01T00:00:00Z")),
    ],
)
def test_concurrent_equivalent_identifiers_commit_exactly_once(
    database_reviewed, type_id, variants
):
    _engine, factory, services, admin = database_reviewed
    dataset, _ = setup_dataset(services, admin, ())
    services.dataset_fields.update_field(admin.id, dataset.key, "id", "ID", type_id, True, False)
    barrier = Barrier(2)

    def create(raw):
        barrier.wait(timeout=10)
        try:
            return services.dataset_records.create_record(admin.id, dataset.key, {"id": raw})
        except RecordConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, variants))
    assert sum(isinstance(result, RecordConflictError) for result in results) == 1
    with factory() as session:
        assert len(list(session.scalars(select(DatasetRecord)))) == 1
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.object_type == "dataset_record"))
        )
        assert len(events) == 1
        assert events[0].action == "created" and events[0].details is None


def test_empty_type_change_and_record_write_race_cannot_convert_values(database_reviewed):
    _engine, _factory, services, admin = database_reviewed
    dataset, _ = setup_dataset(services, admin, ("integer",))
    barrier = Barrier(2)

    def change():
        barrier.wait(timeout=10)
        try:
            return services.dataset_fields.update_field(
                admin.id, dataset.key, "integer", "Value", "boolean", False, True
            )
        except ValidationError as error:
            return error

    def write():
        barrier.wait(timeout=10)
        try:
            return services.dataset_records.create_record(
                admin.id, dataset.key, {"id": "one", "integer": "2"}
            )
        except ValidationError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(change), pool.submit(write)]
        results = [future.result(timeout=20) for future in futures]
    assert sum(isinstance(result, ValidationError) for result in results) == 1
    field = services.dataset_fields.get_field(admin.id, dataset.key, "integer")
    records = services.dataset_records.list_records(admin.id, dataset.key).records
    if field.data_type == "boolean":
        assert records == ()
    else:
        assert field.data_type == "integer"
        assert len(records) == 1
        assert next(v.value for v in records[0].values if v.field_key == "integer") == 2


@pytest.mark.parametrize(
    "type_id,inputs",
    [
        ("integer", ["-100", "-2", "2", "10", "9007199254740993"]),
        ("number", ["-10", "-2", "0.100000000000000001", "0.100000000000000002", "10"]),
        ("float", ["-1.7976931348623157e308", "-2", "5e-324", "2", "10", "1.7976931348623157e308"]),
        ("date", ["0001-01-01", "2000-02-29", "9999-12-31"]),
        ("time", ["00:00:00", "00:00:00.1", "00:00:01", "23:59:59.999999"]),
        (
            "datetime",
            ["0001-01-01T00:00:00", "2024-01-01T00:00:00.1", "9999-12-31T23:59:59.999999"],
        ),
        (
            "timestamp",
            ["2024-01-01T01:00:00+02:00", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00-01:00"],
        ),
    ],
)
def test_persisted_values_have_exact_typed_comparison(database_reviewed, type_id, inputs):
    _engine, _factory, services, admin = database_reviewed
    dataset, _ = setup_dataset(services, admin, (type_id,))
    for index, raw in enumerate(reversed(inputs)):
        services.dataset_records.create_record(
            admin.id, dataset.key, {"id": str(index), type_id: raw}
        )
    values = [
        next(v.value for v in r.values if v.field_key == type_id)
        for r in services.dataset_records.list_records(admin.id, dataset.key).records
    ]
    expected = [canonicalize(type_id, raw)[0] for raw in inputs]
    datatype = get_datatype(type_id)
    assert sorted(values, key=datatype.ordering_key) == expected
    # There is no current Record filter API. The shared typed comparison primitive
    # must preserve range/equality semantics when consuming either adapter's values.
    assert sorted(v for v in values if v >= expected[1]) == expected[1:]
    assert sum(v == expected[1] for v in values) == 1
    if type_id == "number":
        with localcontext() as context:
            context.prec = 2
            artifact = services.dataset_records.export_dataset(
                admin.id, dataset.key, "json", prefer_concept=True
            )
            try:
                output = json.load(artifact.content, parse_float=Decimal)
            finally:
                artifact.content.close()
        assert sorted(row[type_id] for row in output) == expected
        assert expected[2] != expected[3]


def test_pagination_nulls_and_ties_have_stable_record_id_order(reviewed):
    engine, factory, services, admin = reviewed
    dataset, fields = setup_dataset(services, admin, ("integer",))
    records = [
        services.dataset_records.create_record(
            admin.id, dataset.key, {"id": str(i), "integer": "2" if i == 0 else ""}
        )
        for i in range(55)
    ]
    # Simulate incomplete legacy Records: no current service can create a null identifier.
    with factory.begin() as session:
        id_field = session.scalars(select(DatasetField).where(DatasetField.key == "id")).one()
        session.execute(
            delete(DatasetRecordValue).where(DatasetRecordValue.field_id == id_field.id)
        )
    before = state(engine)
    expected = sorted(record.id for record in records)
    for _ in range(2):
        pages = [
            services.dataset_records.list_records(admin.id, dataset.key, page) for page in (1, 2)
        ]
        assert [len(page.records) for page in pages] == [50, 5]
        assert [r.id for page in pages for r in page.records] == expected
    assert state(engine) == before
    assert fields["integer"].data_type == "integer"


def test_stale_update_never_overwrites_exact_values_or_audits(reviewed):
    engine, _factory, services, admin = reviewed
    dataset, _ = setup_dataset(services, admin, ("number",))
    record = services.dataset_records.create_record(
        admin.id, dataset.key, {"id": "one", "number": "0.100000000000000001"}
    )
    services.dataset_records.update_record(
        admin.id, dataset.key, record.id, 1, {"id": "one", "number": "0.100000000000000002"}
    )
    before = state(engine)
    with pytest.raises(StaleRecordError):
        services.dataset_records.update_record(
            admin.id, dataset.key, record.id, 1, {"id": "one", "number": "0.100000000000000002"}
        )
    assert state(engine) == before


@pytest.mark.parametrize("format_name", ["csv", "json", "xlsx"])
def test_export_rejects_out_of_contract_decimal_before_formatting(format_name):
    record = stored(code="safe")
    values = tuple(
        replace(v, value=Decimal("1e1000000000")) if v.data_type == "number" else v
        for v in record.values
    )
    with pytest.raises(ExportGenerationError, match="invalid stored value"):
        generate_export(format_name, FIELDS, [replace(record, values=values)], "Types")


def test_invalid_xlsx_closes_xml_writer_and_removes_temporary_file(monkeypatch):
    workbooks = []
    original = dataset_export.Workbook

    def capture(*args, **kwargs):
        workbook = original(*args, **kwargs)
        workbooks.append(workbook)
        return workbook

    monkeypatch.setattr(dataset_export, "Workbook", capture)
    test_export_rejects_out_of_contract_decimal_before_formatting("xlsx")
    assert len(workbooks) == 1
    sheet = workbooks[0].worksheets[0]
    assert sheet.closed
    assert not Path(sheet._writer.out).exists()


def test_database_type_constraint_matches_only_the_public_catalog(database_reviewed):
    engine, factory, services, admin = database_reviewed
    dataset, fields = setup_dataset(services, admin)
    assert set(fields) == set(DATATYPES)
    from sqlalchemy.exc import IntegrityError

    for invalid in ("decimal", "uuid", "Float"):
        with pytest.raises(IntegrityError), factory.begin() as session:
            session.execute(
                text("UPDATE dataset_fields SET data_type=:type WHERE id=:id"),
                {"type": invalid, "id": fields["string"].id},
            )
    assert services.dataset_fields.get_field(admin.id, dataset.key, "string").data_type == "string"
