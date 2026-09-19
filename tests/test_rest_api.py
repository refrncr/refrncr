"""REF-107 real service/HTTP contracts, run on SQLite and PostgreSQL 16."""

import json
import logging
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as ModelValidationError
from sqlalchemy import event, select
from test_audit_coverage import audit_db, context  # noqa: F401
from test_dataset_versions import NOW, publish, setup

from refrncr.api.contracts import PageQuery, Record
from refrncr.api.routes import create_api
from refrncr.api_keys import ApiKeyService
from refrncr.app import create_application
from refrncr.models import AuditEvent, Dataset
from refrncr.published_contracts import decode_identifier, encode_identifier
from refrncr.record_search import RecordQueryLogFilter
from refrncr.version_persistence import SqlAlchemyVersionOperations


@pytest.fixture
def api(context):  # noqa: F811
    c = context
    setup(c)
    with c.sessions.begin() as session:
        session.get(Dataset, c.d.id).updated_at = NOW - timedelta(seconds=1)
    c.version = publish(c)
    with c.sessions.begin() as session:
        session.get(Dataset, c.d.id).updated_at = NOW
    c.now = [NOW]
    c.keys = ApiKeyService(
        c.s.users.uow, pepper=b"disposable-ref107-test-pepper!!!!!", clock=lambda: c.now[0]
    )
    c.key = c.keys.create_key(c.u.id, "Read integration")
    c.headers = {"Authorization": "Bearer " + c.key.secret}
    c.app = create_application(
        c.s.users,
        c.s.authentication,
        c.s.sessions,
        c.s.teams,
        c.s.memberships,
        authorization=c.s.authorization,
        datasets=c.s.datasets,
        dataset_fields=c.s.dataset_fields,
        dataset_business_keys=c.s.dataset_business_keys,
        dataset_records=c.s.dataset_records,
        dataset_versions=c.service,
        api_keys=c.keys,
    )
    with TestClient(c.app, base_url="https://localhost", raise_server_exceptions=False) as client:
        c.client = client
        yield c


def get(c, suffix="", **kwargs):
    return c.client.get(
        "/api/v1/datasets" + suffix, headers=kwargs.pop("headers", c.headers), **kwargs
    )


def test_endpoints_and_contract(api):
    c = api
    response = get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"] == {"offset": 0, "limit": 50, "total": 1}
    assert body["data"][0]["key"] == c.d.key
    assert body["data"][0]["current_version"]["number"] == 1
    assert body["links"]["self"] == "/api/v1/datasets?offset=0&limit=50"
    response = get(c, "/audit")
    assert response.status_code == 200, response.text
    fields = response.json()["data"]["fields"]
    assert [f["key"] for f in fields] == ["code", "label"]
    assert fields[0] == dict(
        key="code",
        name="Code",
        datatype="string",
        is_identifier=True,
        is_required=True,
        is_unique=True,
        display_position=1,
        definition=None,
        unit=None,
    )
    response = get(c, "/audit/records")
    assert response.status_code == 200, response.text
    row = response.json()["data"][0]
    assert row["values"] == {"code": "a", "label": "Café"}
    assert set(row) == {"identifier", "values"}
    assert c.record.id not in response.text
    lookup = get(c, "/audit/records/" + quote(row["identifier"], safe=""))
    assert lookup.status_code == 200
    assert lookup.json()["data"] == row
    assert lookup.json()["current_version"]["number"] == 1
    assert get(c, "/audit/records/" + c.record.id).status_code == 404


@pytest.mark.parametrize("suffix", ["", "/audit", "/audit/records", "/audit/records/invalid"])
@pytest.mark.parametrize(
    "credential", [None, "", "Bearer unknown", "Basic invalid", "Bearer " + "x" * 10000]
)
def test_uniform_authentication(api, suffix, credential):
    headers = {} if credential is None else {"Authorization": credential}
    response = get(api, suffix, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_api_credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert "no-store" in response.headers["cache-control"]


def test_duplicate_expired_revoked_inactive_keys(api):
    c = api
    duplicate = [("Authorization", c.headers["Authorization"])] * 2
    assert get(c, headers=duplicate).status_code == 401
    c.now[0] += timedelta(days=90)
    assert get(c).status_code == 401
    c.now[0] = NOW
    c.keys.revoke_key(c.u.id, c.key.key.id, 1, confirmed=True)
    assert get(c).status_code == 401
    c.key = c.keys.create_key(c.u.id, "replacement")
    c.headers = {"Authorization": "Bearer " + c.key.secret}
    assert get(c).status_code == 200
    c.s.users.disable_user(c.a.id, c.u.id)
    assert get(c).status_code == 401


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=-1",
        "limit=1.0",
        "limit=1e1",
        "offset=-1",
        "offset=2147483648",
        "limit=01",
        "limit=+1",
        "offset=",
        "limit=2&limit=3",
        "offset=0&offset=1",
        "q=secret",
        "version=1",
        "effective_date=2026-09-10",
        "sort=code",
        "api_key=secret",
    ],
)
def test_fail_closed_query(api, query):
    for suffix in ("", "/audit/records"):
        response = get(api, suffix + "?" + query)
        assert response.status_code == 400, response.text
        assert response.json() == {"error": "invalid_request"}
    assert get(api, "/audit?limit=1").status_code == 400


def test_pagination_current_only_and_publication(api):
    c = api
    first = get(c, "/audit/records")
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "Private concept"})
    assert get(c, "/audit/records").content == first.content
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    assert get(c, "/audit/records").content == first.content
    c.service.clock = lambda: NOW + timedelta(days=1)
    current = get(c, "/audit/records?limit=1")
    assert current.json()["pagination"]["total"] == 2
    assert current.json()["data"][0]["values"]["code"] == "a"
    next_page = c.client.get(current.json()["links"]["next"], headers=c.headers)
    assert next_page.json()["data"][0]["values"]["code"] == "b"
    assert get(c, "/audit/records?offset=2").json()["data"] == []
    assert get(c, "?offset=1").json()["data"] == []
    assert current.headers["etag"] != first.headers["etag"]
    assert current.json()["current_version"]["number"] == scheduled.number


def test_missing_unpublished_cancelled_and_denied(api, monkeypatch):
    c = api
    d = c.s.datasets.create_dataset(c.a.id, "unpublished", "Private", "", c.owner.id)
    assert get(c, "/unpublished").status_code == get(c, "/missing").status_code == 404
    c.s.dataset_fields.create_field(c.a.id, d.key, "code", "Code", "string", True, True, True)
    v = c.service.publish(
        c.a.id,
        d.id,
        NOW.date() + timedelta(days=1),
        "",
        c.s.datasets.get_dataset_for_edit(c.a.id, d.key).revision,
    )
    assert get(c, "/unpublished").status_code == 404
    c.service.cancel(c.a.id, d.id, v.id)
    assert get(c, "/unpublished").status_code == 404
    assert len(get(c).json()["data"]) == 1
    prior = get(c, "/audit")
    monkeypatch.setattr(c.service.authorization, "can_view_dataset", lambda *a, **kw: False)
    denied = get(c, "/audit", headers={**c.headers, "If-None-Match": prior.headers["etag"]})
    assert denied.status_code == 404
    assert denied.content == get(c, "/missing").content


def test_conditional_headers_and_no_content_reload(api, monkeypatch):
    c = api
    row = get(c, "/audit/records").json()["data"][0]
    paths = ["", "/audit", "/audit/records", "/audit/records/" + row["identifier"]]
    previous = {p: get(c, p) for p in paths}

    def fail(*a, **kw):
        raise AssertionError("304 must not load content")

    monkeypatch.setattr(SqlAlchemyVersionOperations, "fields", fail)
    monkeypatch.setattr(SqlAlchemyVersionOperations, "iterate_records", fail)
    for path, response in previous.items():
        assert response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"
        assert response.headers["vary"] == "Authorization, Accept-Encoding"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["last-modified"] == "Thu, 10 Sep 2026 12:00:00 GMT"
        assert not response.headers["etag"].startswith("W/")
        for etag in (
            response.headers["etag"],
            "W/" + response.headers["etag"],
            '"other", ' + response.headers["etag"],
            "*",
        ):
            cached = get(c, path, headers={**c.headers, "If-None-Match": etag})
            assert cached.status_code == 304, cached.text
            assert cached.content == b""
            assert cached.headers["etag"] == response.headers["etag"]
    missing = get(
        c,
        "/audit/records/" + encode_identifier(b"\x00\x00\x00\x08smissing"),
        headers={**c.headers, "If-None-Match": "*"},
    )
    assert missing.status_code == 404


def test_modified_since_precedence_and_metadata_changes(api):
    c = api
    first = get(c, "/audit")
    headers = {**c.headers, "If-Modified-Since": first.headers["last-modified"]}
    assert get(c, "/audit", headers=headers).status_code == 304
    assert get(c, "/audit", headers={**headers, "If-None-Match": '"different"'}).status_code == 200
    assert get(c, "/audit", headers={**headers, "If-Modified-Since": "invalid"}).status_code == 200
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Updated metadata", "new description")
    updated = get(c, "/audit", headers={**headers, "If-None-Match": first.headers["etag"]})
    assert updated.status_code == 200
    assert updated.headers["etag"] != first.headers["etag"]
    assert updated.headers["last-modified"] == first.headers["last-modified"]
    assert get(c, "/audit", headers=headers).status_code == 200


def test_sql_pagination_no_n_plus_one(api, monkeypatch):
    c = api
    c.service.create_concept(c.a.id, c.d.id)
    for i in range(110):
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": f"z{i:03}", "label": "value"})
    publish(c)
    statements = []

    def capture(conn, cursor, statement, parameters, execution_context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(c.engine, "before_cursor_execute", capture)
    original = SqlAlchemyVersionOperations.iterate_records

    def bounded(self, version, *, record_ids=None):
        assert record_ids is not None and len(record_ids) <= 100
        return original(self, version, record_ids=record_ids)

    monkeypatch.setattr(SqlAlchemyVersionOperations, "iterate_records", bounded)
    counts = []
    try:
        for limit in (1, 50, 100):
            statements.clear()
            response = get(c, f"/audit/records?offset=1&limit={limit}")
            assert response.status_code == 200, response.text
            assert len(response.json()["data"]) == limit
            counts.append(len(statements))
            assert any(
                "LIMIT" in s and "OFFSET" in s and "dataset_version_records" in s
                for s in statements
            )
            assert any(" IN (" in s and "dataset_version_values" in s for s in statements)
        assert len(set(counts)) == 1
        print("REF107 record SELECT counts:", counts)
    finally:
        event.remove(c.engine, "before_cursor_execute", capture)


def test_composite_and_all_canonical_datatypes(api):
    c = api
    d = c.s.datasets.create_dataset(c.a.id, "typed", "Typed", "", c.owner.id)
    samples = {
        "code": ("string", "a / café?%"),
        "num": ("integer", "123456789012345678901234567890"),
        "decimal": ("number", "12345678901234567890.123456789012345678"),
        "float": ("float", "1.25"),
        "bool": ("boolean", "false"),
        "date": ("date", "2026-09-10"),
        "time": ("time", "12:34:56.123"),
        "datetime": ("datetime", "2026-09-10T12:34:56"),
        "timestamp": ("timestamp", "2026-09-10T12:34:56.1Z"),
    }
    for key, (kind, _value) in samples.items():
        identifier = key in {"code", "num"}
        c.s.dataset_fields.create_field(
            c.a.id, d.key, key, key, kind, identifier, False, identifier
        )
    c.s.dataset_fields.create_field(c.a.id, d.key, "null", "Null", "string")
    c.s.dataset_records.create_record(c.a.id, d.key, {k: v for k, (_, v) in samples.items()})
    c.service.publish(
        c.a.id, d.id, NOW.date(), "", c.s.datasets.get_dataset_for_edit(c.a.id, d.key).revision
    )
    response = get(c, "/typed/records")
    assert response.status_code == 200, response.text
    data = json.loads(response.text, parse_float=Decimal)["data"][0]
    assert data["values"]["decimal"] == Decimal(samples["decimal"][1])
    assert data["values"]["num"] == int(samples["num"][1])
    assert data["values"]["bool"] is False
    assert data["values"]["null"] is None
    assert data["values"]["timestamp"] == samples["timestamp"][1]
    encoded = data["identifier"]
    assert encode_identifier(decode_identifier(encoded)) == encoded
    assert (
        get(c, "/typed/records/" + quote(encoded, safe="")).json()["data"]["identifier"] == encoded
    )


@pytest.mark.parametrize(
    "kind,values",
    [
        (
            "integer",
            ["-1000000000000000000000", "-10", "-2", "0", "2", "10", "1000000000000000000000"],
        ),
        ("number", ["-10", "-2.1", "-2", "0", "0.000000000000000001", "2", "10"]),
        ("string", ["A", "Z", "a", "z", "é", "😀"]),
        ("timestamp", ["2026-09-10T00:00:00Z", "2026-09-10T00:00:00.1Z", "2026-09-10T00:00:01Z"]),
    ],
)
def test_database_order_matches_existing_browser_order(api, kind, values):
    c = api
    d = c.s.datasets.create_dataset(c.a.id, "ordered", "Ordered", "", c.owner.id)
    c.s.dataset_fields.create_field(c.a.id, d.key, "code", "Code", kind, True, True, True)
    for value in reversed(values):
        c.s.dataset_records.create_record(c.a.id, d.key, {"code": value})
    c.service.publish(
        c.a.id, d.id, NOW.date(), "", c.s.datasets.get_dataset_for_edit(c.a.id, d.key).revision
    )
    from refrncr.datatypes import get_datatype

    expected = [
        get_datatype(kind).text(r.values[0].value)
        for r in c.service.read(c.u.id, d.key).page.records
    ]
    actual = []
    for offset in range(len(values)):
        response = get(c, f"/ordered/records?offset={offset}&limit=1")
        assert response.status_code == 200, response.text
        value = json.loads(response.text, parse_float=Decimal)["data"][0]["values"]["code"]
        actual.append(format(value, "f") if isinstance(value, Decimal) else str(value))
    assert actual == expected


def test_errors_audit_privacy_and_disabled_docs(api, monkeypatch, caplog):
    c = api
    before = get(c, "/audit/records")
    for path in (
        "/api/v1/openapi.json",
        "/api/v1/docs",
        "/api/openapi.json",
        "/api/docs",
        "/openapi.json",
        "/docs",
    ):
        assert c.client.get(path).status_code == 404
    for method in ("post", "put", "patch", "delete"):
        assert getattr(c.client, method)("/api/v1/datasets", headers=c.headers).status_code == 405

    def fail(*a, **kw):
        raise RuntimeError("SQL secret Authorization private record")

    monkeypatch.setattr(c.service, "read_current", fail)
    failed = get(c, "/audit")
    assert failed.status_code == 503
    assert failed.json() == {"error": "service_unavailable"}
    assert c.key.secret not in caplog.text
    with c.sessions() as session:
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.object_type == "api_key"))
        )
        assert all(e.details is None for e in events)
        assert c.key.secret not in repr([e.__dict__ for e in events])
    assert "SQL secret" not in caplog.text + failed.text
    assert before.status_code == 200


def test_pydantic_models_and_source_schema():
    for value in ({"limit": 101}, {"offset": -1}, {"limit": True}, {"limit": "1"}, {"search": "x"}):
        with pytest.raises(ModelValidationError):
            PageQuery(**value)
    with pytest.raises(ModelValidationError):
        Record(identifier="opaque", values={"invalid": []})
    schema = create_api(None, None).openapi()
    assert len(schema["paths"]) == 4
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert all(item["get"]["security"] for item in schema["paths"].values())


def test_api_access_logs_redact_full_target():
    for target in (
        "/api/v1/datasets/private/records/secret?api_key=raw",
        "/api/v1/datasets?token=raw",
        "/api/%76%31/datasets/secret",
    ):
        record = logging.LogRecord(
            "uvicorn.access",
            20,
            "",
            0,
            "%s %s %s %s %s",
            ("client", "GET", target, "1.1", 200),
            None,
        )
        assert RecordQueryLogFilter().filter(record)
        assert "secret" not in record.getMessage() and "raw" not in record.getMessage()
