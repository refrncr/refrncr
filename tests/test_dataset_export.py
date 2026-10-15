import csv
import io
import json
import os
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Event
from types import SimpleNamespace

import pytest
from dataset_helpers import create_owned_dataset, publish_dataset
from openpyxl import load_workbook
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from refrncr.app import create_community_services
from refrncr.dataset_export import (
    ExportArtifact,
    ExportGenerationError,
    externally_ordered_records,
    generate_export,
    verify_artifact_readable,
)
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Dataset
from refrncr.persistence import SqlAlchemyDatasetRecordOperations
from refrncr.ports import RecordValue, StoredRecord
from refrncr.services import DatasetRecordService, NotFoundError, ResourceTypeRegistry
from refrncr.web import SESSION_COOKIE, create_flask_app

HTTPS = "https://localhost"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def postgresql_export_services():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"dataset_export_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        services.users.bootstrap_organisation()
        admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
        yield factory, services, admin
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        administration_engine.dispose()


def field(key, data_type, position):
    return SimpleNamespace(key=key, data_type=data_type, display_position=position)


FIELDS = (
    field("code", "string", 1),
    field("amount", "number", 2),
    field("enabled", "boolean", 3),
    field("effective", "date", 4),
    field("optional", "string", 5),
)


def stored(record_id="r1", code='Å, "quoted"\nline'):
    values = (
        RecordValue("f1", "code", "string", code, b"s" + code.encode(), False),
        RecordValue("f2", "amount", "number", Decimal("123.45"), b"n123.45", False),
        RecordValue("f3", "enabled", "boolean", True, b"b1", False),
        RecordValue("f4", "effective", "date", date(2026, 9, 4), b"d2026-09-04", False),
    )
    return StoredRecord(record_id, "dataset", values, 7)


def content(artifact):
    try:
        return artifact.content.read()
    finally:
        artifact.content.close()


def test_csv_contract_and_formula_neutralization():
    records = [stored(code=prefix + "SUM(A1:A2)") for prefix in ("=", "+", "-", "@")]
    payload = content(generate_export("csv", FIELDS, records, "countries"))
    assert payload.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in payload and payload.endswith(b"\r\n")
    rows = list(csv.reader(io.StringIO(payload[3:].decode("utf-8"), newline="")))
    assert rows[0] == [field.key for field in FIELDS]
    assert [row[0] for row in rows[1:]] == ["'" + record.values[0].value for record in records]
    assert rows[1][1:] == ["123.45", "true", "2026-09-04", ""]


def test_csv_only_neutralizes_strings():
    payload = content(generate_export("csv", FIELDS, [stored()], "countries"))
    assert b",123.45,true,2026-09-04," in payload


@pytest.mark.parametrize("whitespace", [" ", "\t", "\r\n", "\u2003"])
def test_csv_formula_detection_ignores_leading_unicode_whitespace(whitespace):
    payload = content(generate_export("csv", FIELDS, [stored(code=whitespace + "=1+1")], "safe"))
    value = next(csv.reader(io.StringIO(payload[3:].decode(), newline="")))[0]
    assert value == "code"
    rows = list(csv.reader(io.StringIO(payload[3:].decode(), newline="")))
    assert rows[1][0] == "'" + whitespace + "=1+1"


def test_json_contract_preserves_native_types_decimal_and_order():
    payload = content(generate_export("json", FIELDS, [stored()], "countries"))
    text = payload.decode("utf-8")
    assert text.startswith("[{")
    assert "123.45" in text and "1.2345e" not in text.lower()
    assert list(json.loads(text)[0]) == [field.key for field in FIELDS]
    assert json.loads(text) == [
        {
            "code": 'Å, "quoted"\nline',
            "amount": 123.45,
            "enabled": True,
            "effective": "2026-09-04",
            "optional": None,
        }
    ]


@pytest.mark.parametrize("formula_like", ["=1+1", "+1+1", "-1+1", "@SUM(A1:A2)"])
def test_excel_contract_uses_typed_non_formula_cells(formula_like):
    payload = content(generate_export("xlsx", FIELDS, [stored(code=formula_like)], "bad/name"))
    workbook = load_workbook(io.BytesIO(payload), data_only=False)
    assert len(workbook.worksheets) == 1
    sheet = workbook.active
    assert sheet.title == "bad_name"
    assert [cell.value for cell in sheet[1]] == [field.key for field in FIELDS]
    assert sheet["A2"].value == formula_like and sheet["A2"].data_type == "s"
    assert sheet["B2"].data_type == "n"
    assert sheet["C2"].value is True
    assert sheet["D2"].value.date() == date(2026, 9, 4)
    assert sheet["D2"].number_format == "yyyy-mm-dd"
    assert sheet.sheet_state == "visible"
    assert workbook.vba_archive is None and workbook._external_links == []
    workbook.close()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "xl/vbaProject.bin" not in names
        assert "xl/connections.xml" not in names
        relationships = b"".join(archive.read(name) for name in names if name.endswith(".rels"))
        assert b"externalLink" not in relationships


def test_excel_preserves_unsafe_precision_as_literal_text():
    number_field = (field("number", "number", 1),)
    exact = Decimal("99999999999999999999.123456789012345678")
    record = StoredRecord(
        "large",
        "dataset",
        (RecordValue("number", "number", "number", exact, b"n" + str(exact).encode(), False),),
        1,
    )
    payload = content(generate_export("xlsx", number_field, [record], "large"))
    workbook = load_workbook(io.BytesIO(payload), data_only=False)
    assert workbook.active["A2"].value == str(exact)
    assert workbook.active["A2"].data_type == "s"
    workbook.close()


def test_empty_exports_and_invalid_stored_value():
    assert content(generate_export("json", FIELDS, [], "empty")) == b"[]"
    csv_payload = content(generate_export("csv", FIELDS, [], "empty"))
    assert csv_payload.count(b"\r\n") == 1
    invalid = StoredRecord(
        "bad", "dataset", (RecordValue("f2", "amount", "number", "1", b"n1", False),), 1
    )
    try:
        generate_export("json", FIELDS, [invalid], "bad")
    except ExportGenerationError as error:
        assert str(error) == "The Dataset contains an invalid stored value"
    else:
        raise AssertionError("corrupt typed data was accepted")


def test_external_sort_is_global_across_more_than_one_chunk():
    records = [stored(f"{index:04d}", f"{1000 - index:04d}") for index in range(1001)]
    records.extend(
        [
            StoredRecord("0000-a", "dataset", (), 1),
            StoredRecord("0000-b", "dataset", (), 1),
        ]
    )

    def key(record):
        return DatasetRecordService._record_sort_key(record, ("code",))

    ordered = list(externally_ordered_records(iter(records), key))
    assert ordered == sorted(records, key=key)
    assert len({record.id for record in ordered}) == len(records)
    assert ordered[0].id == "1000"
    assert [record.id for record in ordered[-2:]] == ["0000-a", "0000-b"]


def test_json_decimal_tokens_are_exact_native_numbers():
    decimal_field = (field("number", "number", 1),)
    values = (
        "0",
        "0.1",
        "-0.1",
        "0.123456789012345678",
        "99999999999999999999.123456789012345678",
        "1.23",
    )
    records = [
        StoredRecord(
            str(index),
            "dataset",
            (
                RecordValue(
                    "number", "number", "number", Decimal(value), b"n" + value.encode(), False
                ),
            ),
            1,
        )
        for index, value in enumerate(values)
    ]
    payload = content(generate_export("json", decimal_field, records, "decimals"))
    assert payload == ("[" + ",".join(f'{{"number":{value}}}' for value in values) + "]").encode()
    parsed = json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    assert [row["number"] for row in parsed] == [Decimal(value) for value in values]


class TrackedBuffer(io.BytesIO):
    was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


class FailingReadBuffer(TrackedBuffer):
    reads = 0

    def read(self, size=-1):
        self.reads += 1
        if self.reads == 2:
            raise OSError("injected spool read failure")
        return super().read(1 if size != 0 else size)


def test_serializer_failure_closes_output_spool_and_is_not_masked(monkeypatch):
    spool = TrackedBuffer()
    monkeypatch.setattr(
        "refrncr.dataset_export.tempfile.SpooledTemporaryFile", lambda **_kwargs: spool
    )

    def failing_records():
        yield stored()
        raise RuntimeError("injected serializer failure")

    with pytest.raises(RuntimeError, match="injected serializer failure"):
        generate_export("json", FIELDS, failing_records(), "failure")
    assert spool.was_closed


def test_workbook_finalization_failure_closes_output_spool(monkeypatch):
    spool = TrackedBuffer()
    monkeypatch.setattr(
        "refrncr.dataset_export.tempfile.SpooledTemporaryFile", lambda **_kwargs: spool
    )
    monkeypatch.setattr(
        "refrncr.dataset_export.Workbook.save",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("xlsx save failed")),
    )
    with pytest.raises(RuntimeError, match="xlsx save failed"):
        generate_export("xlsx", FIELDS, [stored()], "failure")
    assert spool.was_closed


def test_spool_readback_failure_is_detected_and_cleaned_before_response():
    spool = FailingReadBuffer(b"complete artifact")
    artifact = ExportArtifact(spool, "application/json", "json")
    with pytest.raises(OSError, match="injected spool read failure"):
        verify_artifact_readable(artifact)
    assert spool.was_closed


def test_early_sort_disconnect_closes_every_temporary_run(monkeypatch):
    runs = []

    def tracked_run(**_kwargs):
        run = TrackedBuffer()
        runs.append(run)
        return run

    monkeypatch.setattr("refrncr.dataset_export.SORT_CHUNK_RECORDS", 2)
    monkeypatch.setattr("refrncr.dataset_export.tempfile.TemporaryFile", tracked_run)
    records = [stored(str(index), str(9 - index)) for index in range(10)]
    ordered = externally_ordered_records(records, lambda record: record.values[0].value)
    next(ordered)
    ordered.close()
    assert len(runs) == 5
    assert all(run.was_closed for run in runs)


@pytest.mark.parametrize("mutation", ["create", "update", "delete", "reorder"])
def test_postgresql_export_is_one_snapshot_during_mutation(
    postgresql_export_services, monkeypatch, mutation
):
    factory, services, admin = postgresql_export_services
    dataset = create_owned_dataset(services.datasets, admin.id, "snapshot", "Snapshot", "")
    code = services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", True, False
    )
    label = services.dataset_fields.create_field(
        admin.id, dataset.key, "label", "Label", "string", False, False
    )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    first = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "a", "label": "old-a"}
    )
    second = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "b", "label": "old-b"}
    )
    with factory() as session:
        before_audits = session.scalar(select(func.count(AuditEvent.id)))

    snapshot_open = Event()
    continue_export = Event()
    original = SqlAlchemyDatasetRecordOperations.iterate_all

    def synchronized_iterate(operation, dataset_id):
        iterator = original(operation, dataset_id)
        first_record = next(iterator, None)
        snapshot_open.set()
        assert continue_export.wait(timeout=10)
        if first_record is not None:
            yield first_record
        yield from iterator

    monkeypatch.setattr(SqlAlchemyDatasetRecordOperations, "iterate_all", synchronized_iterate)

    def export():
        return content(
            services.dataset_records.export_dataset(
                admin.id, dataset.key, "json", prefer_concept=True
            )
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(export)
        assert snapshot_open.wait(timeout=10)
        if mutation == "create":
            services.dataset_records.create_record(
                admin.id, dataset.key, {"code": "c", "label": "new-c"}
            )
        elif mutation == "update":
            services.dataset_records.update_record(
                admin.id,
                dataset.key,
                first.id,
                first.revision,
                {"code": "a", "label": "new-a"},
            )
        elif mutation == "delete":
            services.dataset_records.delete_record(
                admin.id, dataset.key, second.id, second.revision
            )
        else:
            services.dataset_fields.replace_order(
                admin.id, dataset.key, (code.id, label.id), (label.id, code.id)
            )
        continue_export.set()
        exported = json.loads(pending.result(timeout=10))

    assert exported == [
        {"code": "a", "label": "old-a"},
        {"code": "b", "label": "old-b"},
    ]
    with factory() as session:
        assert session.scalar(select(func.count(AuditEvent.id))) == before_audits + 2


def browser_setup():
    engine = create_database_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    app = create_flask_app(
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
    )
    client = app.test_client()
    token, _session = services.sessions.create(admin)
    client.set_cookie(SESSION_COOKIE, token, domain="localhost", secure=True)
    return factory, services, admin, client


def test_browser_download_headers_parameter_validation_and_request_audit():
    factory, services, admin, client = browser_setup()
    dataset = create_owned_dataset(services.datasets, admin.id, "countries", "Countries", "")
    services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", True, False
    )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "NL"})

    with factory() as session:
        audits_before = session.scalar(select(func.count(AuditEvent.id)))
    response = client.get("/datasets/countries/concept/records/export?format=json", base_url=HTTPS)
    assert response.status_code == 200
    assert response.content_type == "application/json; charset=utf-8"
    assert response.headers["Content-Disposition"] == "attachment; filename=countries.json"
    assert json.loads(response.data) == [{"code": "NL"}]
    with factory() as session:
        assert session.scalar(select(func.count(AuditEvent.id))) == audits_before + 1

    csv_download = client.get(
        "/datasets/countries/concept/records/export?format=csv", base_url=HTTPS
    )
    assert csv_download.headers["Content-Disposition"] == "attachment; filename=countries.csv"
    assert list(csv.reader(io.StringIO(csv_download.data[3:].decode("utf-8"), newline=""))) == [
        ["code"],
        ["NL"],
    ]

    excel_download = client.get(
        "/datasets/countries/concept/records/export?format=xlsx", base_url=HTTPS
    )
    assert excel_download.headers["Content-Disposition"] == "attachment; filename=countries.xlsx"
    workbook = load_workbook(io.BytesIO(excel_download.data), data_only=False)
    assert list(workbook.active.values) == [("code",), ("NL",)]
    workbook.close()

    for suffix in ("", "?format=pdf", "?format=csv&format=json", "?format="):
        rejected = client.get("/datasets/countries/concept/records/export" + suffix, base_url=HTTPS)
        assert rejected.status_code == 400


def test_response_construction_failure_closes_generated_artifact(monkeypatch):
    _factory, services, admin, client = browser_setup()
    dataset = create_owned_dataset(services.datasets, admin.id, "response-failure", "Failure", "")
    spool = TrackedBuffer(b"complete")
    monkeypatch.setattr(
        services.dataset_records,
        "export_dataset",
        lambda *_args, **_kwargs: ExportArtifact(spool, "application/json", "json"),
    )
    monkeypatch.setattr(
        "refrncr.web.send_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("response failed")),
    )
    response = client.get(f"/datasets/{dataset.key}/records/export?format=json", base_url=HTTPS)
    assert response.status_code == 500
    assert b"response failed" not in response.data
    assert spool.was_closed


def test_successful_response_close_releases_generated_artifact(monkeypatch):
    _factory, services, admin, client = browser_setup()
    dataset = create_owned_dataset(services.datasets, admin.id, "response-close", "Close", "")
    spool = TrackedBuffer(b"[]")
    monkeypatch.setattr(
        services.dataset_records,
        "export_dataset",
        lambda *_args, **_kwargs: ExportArtifact(spool, "application/json", "json"),
    )
    response = client.get(f"/datasets/{dataset.key}/records/export?format=json", base_url=HTTPS)
    assert response.status_code == 200 and response.data == b"[]"
    response.close()
    assert spool.was_closed


@pytest.mark.parametrize("permission", ["viewer", "editor", "admin"])
def test_export_requires_explicit_access_despite_legacy_roles(permission):
    _factory, services, admin, _client = browser_setup()
    dataset = create_owned_dataset(services.datasets, admin.id, "allowed", "Allowed", "")
    user = services.users.create_user(
        admin.id, f"dataset-{permission}@example.com", "Exporter", "valid password"
    )
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    publish_dataset(services, admin.id, dataset)
    with pytest.raises(NotFoundError):
        services.dataset_records.export_dataset(user.id, dataset.key, "json")
    services.datasets.grant_export_access(admin.id, dataset.key, "user", user.id)
    artifact = services.dataset_records.export_dataset(user.id, dataset.key, "json")
    assert content(artifact) == b"[]"


def test_unauthorized_and_cross_dataset_export_are_non_disclosing():
    _factory, services, admin, _client = browser_setup()
    allowed = create_owned_dataset(services.datasets, admin.id, "allowed", "Allowed", "")
    hidden = create_owned_dataset(services.datasets, admin.id, "hidden", "Hidden", "")
    user = services.users.create_user(admin.id, "limited@example.com", "Limited", "valid password")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", allowed.id, "viewer")
    for key in (hidden.key, "missing", "MALFORMED"):
        with pytest.raises(NotFoundError, match="^Dataset not found$"):
            services.dataset_records.export_dataset(user.id, key, "csv")


def test_dataset_ownership_without_grant_does_not_authorize_export():
    factory, services, admin, _client = browser_setup()
    dataset = create_owned_dataset(services.datasets, admin.id, "owned", "Owned", "")
    user = services.users.create_user(admin.id, "owner@example.com", "Owner", "valid password")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=user.id)
        )
    with pytest.raises(NotFoundError, match="^Dataset not found$"):
        services.dataset_records.export_dataset(user.id, dataset.key, "json")
