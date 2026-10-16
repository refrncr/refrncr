"""Exercise the catalog through real services, transactions and both adapters."""

import csv
import io
import json
from decimal import Decimal

import pytest
from dataset_helpers import create_owned_dataset
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from support import CASES, PASSWORD

from refrncr.datatypes import DATATYPES, canonicalize, get_datatype
from refrncr.models import AuditEvent, Dataset, DatasetField, DatasetRecord
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import (
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    StaleRecordError,
    ValidationError,
)
from refrncr.web import create_flask_app


@pytest.fixture(params=["record_services"])
def datatype_services(request):
    return request.getfixturevalue(request.param)


def setup_dataset(services, admin, types=DATATYPES):
    dataset = create_owned_dataset(services.datasets, admin.id, "types", "Types", "")
    services.dataset_fields.create_field(
        admin.id, dataset.key, "id", "ID", "string", True, False, True
    )
    fields = {}
    for type_id in types:
        fields[type_id] = services.dataset_fields.create_field(
            admin.id, dataset.key, type_id, get_datatype(type_id).label, type_id, False, True
        )
    return dataset, fields


def snapshot(factory):
    with factory() as session:
        return (
            list(session.execute(select(DatasetRecord.id, DatasetRecord.revision))),
            list(session.execute(select(Dataset.content_updated_at))),
            list(
                session.execute(
                    select(AuditEvent.id, AuditEvent.action, AuditEvent.details).where(
                        AuditEvent.object_type != "security"
                    )
                )
            ),
        )


def test_all_types_persist_read_export_and_canonical_noop(datatype_services):
    _, factory, services, admin = datatype_services
    dataset, fields = setup_dataset(services, admin)
    submitted = {type_id: raw for type_id, raw, _text, _value in CASES} | {"id": "one"}
    record = services.dataset_records.create_record(admin.id, dataset.key, submitted)
    expected = {type_id: value for type_id, _raw, _text, value in CASES} | {"id": "one"}
    for actual in (
        record,
        services.dataset_records.get_record_for_edit(admin.id, dataset.key, record.id),
        services.dataset_records.list_records(admin.id, dataset.key).records[0],
    ):
        assert {value.field_key: value.value for value in actual.values} == expected
    before = snapshot(factory)
    normalized = {type_id: text for type_id, _raw, text, _value in CASES} | {"id": "one"}
    assert (
        services.dataset_records.update_record(
            admin.id, dataset.key, record.id, 1, normalized
        ).revision
        == 1
    )
    for type_id, field in fields.items():
        services.dataset_fields.update_field(
            admin.id, dataset.key, field.key, field.name, type_id, False, True
        )
    assert snapshot(factory) == before
    with pytest.raises(StaleRecordError):
        services.dataset_records.update_record(admin.id, dataset.key, record.id, 2, normalized)
    assert snapshot(factory) == before

    for format_name in ("csv", "json", "xlsx"):
        artifact = services.dataset_records.export_dataset(
            admin.id, dataset.key, format_name, prefer_concept=True
        )
        try:
            payload = artifact.content.read()
        finally:
            artifact.content.close()
        if format_name == "json":
            assert json.loads(payload, parse_float=Decimal)[0]["number"] == expected["number"]
            assert json.loads(payload)[0]["timestamp"] == normalized["timestamp"]
            assert json.loads(payload)[0]["integer"] == expected["integer"]
        elif format_name == "csv":
            rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
            assert rows == [normalized]
        else:
            workbook = load_workbook(io.BytesIO(payload))
            rows = list(workbook.active.values)
            values = dict(zip(rows[0], rows[1], strict=True))
            for type_id in ("integer", "float", "time", "datetime", "timestamp"):
                assert values[type_id] == normalized[type_id]
            workbook.close()


@pytest.mark.parametrize("type_id,raw,text,value", CASES)
def test_empty_field_changes_and_populated_field_guards(
    datatype_services, type_id, raw, text, value
):
    _, factory, services, admin = datatype_services
    dataset, _fields = setup_dataset(services, admin, ())
    field = services.dataset_fields.create_field(admin.id, dataset.key, "value", "Value", "string")
    first = services.dataset_records.create_record(admin.id, dataset.key, {"id": "one"})
    changed = services.dataset_fields.update_field_and_business_key_membership(
        admin.id, dataset.key, field.key, field.name, type_id, False, False, False
    )
    assert changed.data_type == type_id
    services.dataset_records.update_record(
        admin.id, dataset.key, first.id, 1, {"id": "one", "value": raw}
    )
    before = snapshot(factory)
    target = "boolean" if type_id == "string" else "string"
    for combined in (False, True):
        method = (
            services.dataset_fields.update_field_and_business_key_membership
            if combined
            else services.dataset_fields.update_field
        )
        args = (False,) if combined else ()
        with pytest.raises(ValidationError, match="stored values"):
            method(admin.id, dataset.key, field.key, field.name, target, False, False, *args)
    assert snapshot(factory) == before
    assert services.dataset_fields.get_field(admin.id, dataset.key, field.key).data_type == type_id


@pytest.mark.parametrize("type_id,raw,text,value", CASES)
def test_unique_values_use_canonical_equivalence(datatype_services, type_id, raw, text, value):
    _, factory, services, admin = datatype_services
    dataset, _fields = setup_dataset(services, admin, (type_id,))
    services.dataset_records.create_record(admin.id, dataset.key, {"id": "one", type_id: raw})
    before = snapshot(factory)
    with pytest.raises(RecordConflictError, match="unique"):
        services.dataset_records.create_record(admin.id, dataset.key, {"id": "two", type_id: text})
    assert snapshot(factory) == before
    # Null is absent and never occupies a uniqueness key.
    for key in ("two", "three"):
        services.dataset_records.create_record(admin.id, dataset.key, {"id": key})


@pytest.mark.parametrize("entrypoint", ["create", "update", "combined", "identifier"])
def test_float_identifier_rejected_by_all_service_entrypoints(datatype_services, entrypoint):
    _, factory, services, admin = datatype_services
    dataset, _fields = setup_dataset(services, admin, ())
    field = services.dataset_fields.create_field(
        admin.id, dataset.key, "measurement", "Measurement", "float", True
    )
    before = snapshot(factory)
    with pytest.raises(ValidationError, match="Float"):
        if entrypoint == "create":
            services.dataset_fields.create_field(
                admin.id, dataset.key, "bad", "Bad", "float", True, False, True
            )
        elif entrypoint == "update":
            services.dataset_fields.update_field(
                admin.id, dataset.key, "id", "ID", "float", True, False
            )
        elif entrypoint == "combined":
            services.dataset_fields.update_field_and_business_key_membership(
                admin.id, dataset.key, field.key, field.name, "float", True, False, True
            )
        else:
            services.dataset_business_keys.set_business_key(
                admin.id, dataset.key, ("id", field.key)
            )
    assert snapshot(factory) == before
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == (
        "id",
    )


@pytest.mark.parametrize("type_id", ["integer", "number", "time", "datetime", "timestamp"])
def test_identifiers_are_typed_and_equivalent(datatype_services, type_id):
    _, _factory, services, admin = datatype_services
    dataset = create_owned_dataset(services.datasets, admin.id, "ordered", "Ordered", "")
    services.dataset_fields.create_field(
        admin.id, dataset.key, "value", "Value", type_id, True, False, True
    )
    inputs = {
        "integer": ["-10", "-2", "2", "10"],
        "number": ["-10", "-2.1", "2.01", "10"],
        "time": ["00:00:00", "00:00:00.1", "12:00:00"],
        "datetime": ["0001-01-01T00:00:00", "2024-01-01T00:00:00.1", "2024-01-01T00:00:01"],
        "timestamp": ["2024-01-01T01:00:00+02:00", "2024-01-01T00:00:00Z"],
    }[type_id]
    for raw in reversed(inputs):
        services.dataset_records.create_record(admin.id, dataset.key, {"value": raw})
    assert [
        record.values[0].value
        for record in services.dataset_records.list_records(admin.id, dataset.key).records
    ] == [canonicalize(type_id, raw)[0] for raw in inputs]
    equivalent = get_datatype(type_id).text(canonicalize(type_id, inputs[0])[0])
    with pytest.raises(RecordConflictError, match="identifier"):
        services.dataset_records.create_record(admin.id, dataset.key, {"value": equivalent})


@pytest.mark.parametrize(
    "operation", ["field_create", "field_update", "record_create", "record_update"]
)
def test_audit_insert_failure_rolls_back_all_business_changes(
    datatype_services, monkeypatch, operation
):
    _, factory, services, admin = datatype_services
    dataset, fields = setup_dataset(services, admin, ("timestamp",))
    record = services.dataset_records.create_record(admin.id, dataset.key, {"id": "one"})
    before = snapshot(factory)
    original = SqlAlchemyUnitOfWork.add_audit

    def fail(self, **values):
        original(self, **{**values, "actor_user_id": "missing-actor"})

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    with pytest.raises(IntegrityError):
        if operation == "field_create":
            services.dataset_fields.create_field(admin.id, dataset.key, "new", "New", "float")
        elif operation == "field_update":
            services.dataset_fields.update_field(
                admin.id, dataset.key, "timestamp", "Timestamp", "datetime", False, True
            )
        elif operation == "record_create":
            services.dataset_records.create_record(
                admin.id, dataset.key, {"id": "two", "timestamp": "2024-01-01T00:00:00Z"}
            )
        else:
            services.dataset_records.update_record(
                admin.id,
                dataset.key,
                record.id,
                1,
                {"id": "one", "timestamp": "2024-01-01T00:00:00Z"},
            )
    assert snapshot(factory) == before
    with factory() as session:
        assert session.get(DatasetField, fields["timestamp"].id).data_type == "timestamp"
        assert session.scalar(select(func.count(DatasetField.id))) == 2


def test_denied_and_invalid_calls_never_audit(datatype_services):
    _, factory, services, admin = datatype_services
    dataset, _fields = setup_dataset(services, admin, ("float",))
    user = services.users.create_user(admin.id, "reader@example.com", "Reader", PASSWORD)
    before = snapshot(factory)
    with pytest.raises(NotFoundError):
        services.dataset_records.create_record(user.id, dataset.key, {"id": "one", "float": "1"})
    with pytest.raises(NotFoundError):
        services.dataset_fields.update_field(
            user.id, dataset.key, "float", "Float", "integer", False, True
        )
    with pytest.raises(RecordValidationError):
        services.dataset_records.create_record(admin.id, dataset.key, {"id": "one", "float": "NaN"})
    for type_id in ("decimal", "unknown", "Float"):
        with pytest.raises(ValidationError):
            services.dataset_fields.create_field(admin.id, dataset.key, "bad", "Bad", type_id)
        with pytest.raises(ValidationError):
            services.dataset_fields.update_field(
                admin.id, dataset.key, "float", "Float", type_id, False, True
            )
    assert snapshot(factory) == before


@pytest.mark.parametrize("format_name", ["csv", "xlsx"])
def test_existing_import_adapters_share_all_nine_canonical_contracts(
    datatype_services, format_name
):
    _, factory, services, admin = datatype_services
    dataset, _fields = setup_dataset(services, admin)
    headers = ["id", *DATATYPES]
    values = ["imported", *(raw for _type_id, raw, _text, _value in CASES)]
    if format_name == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerow(values)
        payload = output.getvalue().encode()
    else:
        output = io.BytesIO()
        workbook = Workbook()
        workbook.active.append(headers)
        workbook.active.append(values)
        workbook.save(output)
        workbook.close()
        payload = output.getvalue()
    importer = services.record_imports
    state, _source, assistance = importer.begin(
        admin.id, "datatype-session", dataset.key, "types." + format_name, payload
    )
    assert assistance.mapping == tuple(headers)
    preview = importer.preview(
        admin.id, "datatype-session", dataset.key, state.token, tuple(headers)
    )
    assert preview.valid
    assert importer.confirm(admin.id, "datatype-session", dataset.key, state.token) == 1
    record = services.dataset_records.list_records(admin.id, dataset.key).records[0]
    assert {value.field_key: value.value for value in record.values} == {
        "id": "imported",
        **{type_id: value for type_id, _raw, _text, value in CASES},
    }
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset_record")
            )
            == 0
        )
        summary = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "dataset.records_imported")
        ).one()
        assert summary.actor_user_id == admin.id
        assert summary.details == {
            "format": "CSV" if format_name == "csv" else "Excel",
            "record_count": 1,
            "rejected_row_count": 0,
        }


def browser_client(services, admin):
    client = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        dataset_records=services.dataset_records,
    ).test_client()
    response = client.get("/login", base_url="https://localhost")
    csrf = response.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    assert (
        client.post(
            "/login",
            base_url="https://localhost",
            data={
                "email": admin.email,
                "password": PASSWORD,
                "csrf_token": csrf,
            },
        ).status_code
        == 302
    )
    return client


def test_browser_catalog_float_guard_trusted_actor_and_canonical_edit(record_services):
    _, factory, services, admin = record_services
    dataset, _fields = setup_dataset(services, admin, ())
    client = browser_client(services, admin)
    page = client.get("/datasets/types/fields/new", base_url="https://localhost").get_data(
        as_text=True
    )
    csrf = page.split("name='csrf_token' value='")[1].split("'", 1)[0]
    for datatype in DATATYPES.values():
        assert f">{datatype.label}</option>" in page
        assert (
            client.post(
                "/datasets/types/fields",
                base_url="https://localhost",
                data={
                    "key": datatype.id,
                    "name": datatype.label,
                    "data_type": datatype.id,
                    "csrf_token": csrf,
                    "actor_id": "forged-actor",
                },
            ).status_code
            == 302
        )
    before = snapshot(factory)
    for datatype in DATATYPES.values():
        page = client.get(
            f"/datasets/types/fields/{datatype.id}/edit", base_url="https://localhost"
        ).get_data(as_text=True)
        assert f" selected>{datatype.label}</option>" in page
        assert (
            client.post(
                f"/datasets/types/fields/{datatype.id}",
                base_url="https://localhost",
                data={"name": datatype.label, "data_type": datatype.id, "csrf_token": csrf},
            ).status_code
            == 302
        )
    assert snapshot(factory) == before
    for token, expected in ((csrf, 400), ("invalid", 403)):
        response = client.post(
            "/datasets/types/fields/float",
            base_url="https://localhost",
            data={
                "name": "Float",
                "data_type": "float",
                "is_required": "on",
                "is_business_key_component": "on",
                "csrf_token": token,
            },
        )
        assert response.status_code == expected
    assert snapshot(factory) == before
    submitted = {type_id: raw for type_id, raw, _text, _value in CASES} | {"id": "one"}
    record = services.dataset_records.create_record(admin.id, dataset.key, submitted)
    body = client.get(
        f"/datasets/types/records/{record.id}/edit", base_url="https://localhost"
    ).get_data(as_text=True)
    for type_id, _raw, text, _value in CASES:
        if type_id != "boolean":
            assert f"value='{text}'" in body
    # The embedded Record-sidepanel editor is a separate rendering path from
    # /records/<id>/edit and must round-trip the same canonical text.
    page = client.get("/datasets/types/concept/records", base_url="https://localhost").get_data(
        as_text=True
    )
    for type_id, _raw, text, _value in CASES:
        if type_id != "boolean":
            input_type = "date" if type_id == "date" else "text"
            assert f"name='{type_id}' type='{input_type}' value='{text}'" in page
    assert "2024-02-29 01:02:03" not in page
    assert "2024-02-29 23:30:00" not in page
    with factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.object_type.in_(["dataset_field", "dataset_record"])
                )
            )
        )
        assert events and all(event.actor_user_id == admin.id for event in events)
        assert all(event.details is None for event in events)
