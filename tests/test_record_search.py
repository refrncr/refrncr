"""REF-136 basic search, complete version scope and safe native GET contracts."""

from datetime import timedelta
from logging import LogRecord
from time import perf_counter
from urllib.parse import urlencode

import pytest
from sqlalchemy import event
from support import NOW, html_elements, publish, request, setup, snapshot
from support import dataset_version_client as client

from refrncr.datatypes import DATATYPES
from refrncr.record_search import RecordQueryLogFilter
from refrncr.services import NotFoundError

SAMPLES = {
    "string": ("Amsterdam", "Rotterdam", "ams"),
    "integer": ("9" * 1000, "-10", "2026"),
    "number": ("99999999999999999999.123456789123456789", "-0.000000000000000001", "17.50"),
    "float": ("1.25e+30", "-1e-300", "6.7e7"),
    "boolean": ("true", "false", "true"),
    "date": ("2026-09-15", "2024-02-29", "2026-09-15"),
    "time": ("23:59:59.999999", "00:00:00", "15:20:30"),
    "datetime": ("2026-09-15T23:59:59.999999", "2024-02-29T00:00:00", "2026-09-15T00:00:00"),
    "timestamp": ("2026-09-15T23:59:59.999999Z", "2024-02-29T00:00:00Z", "2026-09-15T00:00:00Z"),
}


def read(c, **kwargs):
    return c.service.read(c.a.id, c.d.key, prefer_concept=True, **kwargs).page


def test_general_search_only_string_fields_including_identifiers(context):
    c = context
    setup(c, populated=False)
    values = {"code": "ID-AMS", "label": "Straße Café 100%_literal"}
    for datatype, (_high, _low, sample) in SAMPLES.items():
        if datatype == "string":
            continue
        c.s.dataset_fields.create_field(c.a.id, c.d.key, datatype, datatype, datatype)
        values[datatype] = sample
    c.s.dataset_records.create_record(c.a.id, c.d.key, values)
    for term in ("ams", "STRASSE", "Cafe\u0301", "%_", "literal"):
        assert read(c, search=term).total == 1
    for term in ("2026", "17.50", "true", "15:20", "6.7", "not there", "%' OR 1=1 --"):
        assert read(c, search=term).total == 0


@pytest.mark.parametrize(
    "datatype",
    [kind for kind, info in DATATYPES.items() if kind != "string" and info.identifier_eligible],
)
def test_non_string_identifier_is_not_general_search_text(context, datatype):
    c = context
    setup(c, populated=False)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "code", "Code", datatype, True, True)
    value = SAMPLES[datatype][2]
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": value})
    assert read(c, search=value[:200]).total == 0


@pytest.mark.slow
def test_and_status_pagination_counts_and_url_roundtrip(context):
    c = context
    setup(c, populated=False)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "n", "Number", "integer")
    records = [
        c.s.dataset_records.create_record(
            c.a.id, c.d.key, {"code": f"id-{i:03}", "label": "Amsterdam", "n": str(i)}
        )
        for i in range(65)
    ]
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id,
        c.d.key,
        records[0].id,
        1,
        {"code": "id-000", "label": "Amsterdam edited", "n": "0"},
    )
    c.s.dataset_records.delete_record(c.a.id, c.d.key, records[-1].id, 1)
    added = c.s.dataset_records.create_record(
        c.a.id, c.d.key, {"code": "id-065", "label": "Amsterdam added", "n": "65"}
    )
    page = read(c, search="AMS")
    assert page.total == 66 and len(page.records) == 50
    page2 = read(c, search="AMS", page=2)
    assert {r.id for r in (*page.records, *page2.records)} == {r.id for r in (*records, added)}
    assert [r.id for r in read(c, search="AMS", record_status="added").records] == [added.id]
    assert [r.id for r in read(c, search="AMS", record_status="edited").records] == [records[0].id]
    assert read(c, search="AMS", record_status="deleted").total == 1
    assert read(c, search="AMS", record_status="edited,deleted").total == 2
    # Change toggle counts follow the search term, whatever filters are selected.
    for status in ("added", "edited", "deleted", "added,edited,deleted", "all"):
        view = c.service.read(
            c.a.id, c.d.key, None, prefer_concept=True, search="AMS", record_status=status
        )
        assert view.status_counts == (1, 1, 1)
    missing = c.service.read(c.a.id, c.d.key, None, prefer_concept=True, search="missing")
    assert missing.status_counts == (0, 0, 0)
    single = c.service.read(c.a.id, c.d.key, None, prefer_concept=True, search="id-064")
    assert single.status_counts == (0, 0, 1)
    browser, _ = client(c)
    path = f"/datasets/{c.d.key}/concept/records"
    params = {"q": "AMS"}
    response = request(browser, "get", path + "?" + urlencode(params))
    assert response.status_code == 200 and "of 66" in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    for key, value in params.items():
        assert f"{key}={value}" in response.text or key.startswith("f")
    roundtrip = request(browser, "get", path + "?" + urlencode(params | {"page": 2}))
    assert "Page 2 of 2" in roundtrip.text and "of 66" in roundtrip.text
    reset = request(browser, "get", path + "?" + urlencode(params | {"page": 2, "action": "apply"}))
    assert reset.status_code == 303 and "page=" not in reset.location
    assert request(browser, "get", reset.location).status_code == 200
    zero = request(browser, "get", path + "?q=missing")
    assert (
        "No matching records" in zero.text
        and "of 0" in zero.text
        and "Clear all criteria" in zero.text
    )
    default = request(browser, "get", path)
    assert "of 66" in default.text
    toggles = default.text.split("data-record-status-form>")[1].split("</form>")[0]
    assert sum(attrs.get("aria-pressed") == "false" for _, attrs in html_elements(toggles)) == 3
    for label, value in (("Added", "added"), ("Edited", "edited"), ("Deleted", "deleted")):
        assert f"name='record_status' value='{value}'>\n    {label}" in toggles
    edited = request(browser, "get", path + "?record_status=edited").text
    toggles = edited.split("data-record-status-form>")[1].split("</form>")[0]
    edited_button = (
        "aria-pressed='true'\n    type='submit' name='record_status' value='all'>\n    Edited"
    )
    assert edited_button in toggles
    assert "name='record_status' value='edited,deleted'>\n    Deleted" in toggles


BAD_QUERIES = (
    "q=a&q=b",
    "surprise=private",
    "page=01",
    "page=-1",
    "page=1.0",
    "page=1&page=2",
    "f0_field=label&f0_op=contains&f0_value=Caf",
    "action=add&new_field=label",
    "f0_field=label",
    "f1_field=label&f1_op=empty",
    "f5_field=label&f5_op=empty",
    "f0_field=private&f0_op=empty",
    "f0_field=label&f0_field=code&f0_op=empty",
    "f0_field=label&f0_op=lt&f0_value=1",
    "f0_field=label&f0_op=between&f0_value=1",
    "f0_field=label&f0_op=contains",
    "f0_field=label&f0_op=empty&f0_value=secret",
    "f0_field=label&f0_op=empty&f1_field=label&f1_op=empty",
    "status=Added",
    "import_results=1&status=Added&status=Added",
    "record_status=bad",
    "record_status=active&record_status=deleted",
    "import_results=2",
    "status_selection=2",
    "action=remove:9",
    "action=oops",
    "new_field=label",
    "action=add",
    "q=%00private",
    "q=" + "x" * 201,
    "q=" + "x" * 33000,
)


@pytest.mark.parametrize("query", BAD_QUERIES)
def test_fail_closed_query_envelope_has_no_partial_results(context, query):
    c = context
    setup(c)
    browser, _ = client(c)
    before = snapshot(c)
    response = request(browser, "get", f"/datasets/{c.d.key}/concept/records?{query}")
    assert response.status_code == 400
    assert (
        "Café" not in response.text
        and "private" not in response.text
        and "secret" not in response.text
    )
    assert "Clear all" in response.text
    assert snapshot(c) == before


def test_current_scheduled_concept_and_unauthorized_queries_are_isolated(context):
    c = context
    setup(c)
    current = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Scheduled secret"}
    )
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 2, {"code": "a", "label": "Concept secret"}
    )
    for version_id, word in ((current.id, "Café"), (scheduled.id, "Scheduled")):
        assert c.service.read(c.a.id, c.d.key, version_id, search=word).page.total == 1
        assert c.service.read(c.a.id, c.d.key, version_id, search="Concept").page.total == 0
    assert read(c, search="Concept").total == 1
    with pytest.raises(NotFoundError):
        c.service.read(c.u.id, c.d.key, scheduled.id, search="private")
    # prefer_concept is an internal preference: ordinary service readers fall
    # back to Current. Explicit Concept HTTP routes independently deny access.
    assert c.service.read(c.u.id, c.d.key, prefer_concept=True, search="Concept").page.total == 0
    ordinary, _ = client(c, c.u)
    assert (
        request(ordinary, "get", f"/datasets/{c.d.key}/concept/records?q=private").status_code
        == 404
    )
    assert c.service.read(c.u.id, c.d.key, search="secret").page.total == 0


def test_recreated_field_and_old_datatype_never_reinterpret_retained_values(context):
    c = context
    setup(c)
    c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
    c.s.dataset_fields.delete_field(c.a.id, c.d.key, "label")
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "label", "Replacement", "integer")
    assert read(c, search="Café", record_status="deleted").total == 0


def test_search_query_count_does_not_grow_per_record(context, record_property):
    c = context
    setup(c)
    counts = []
    for size in (1, 30):
        if size > 1:
            for index in range(size - 1):
                c.s.dataset_records.create_record(
                    c.a.id, c.d.key, {"code": str(index), "label": "Café"}
                )
        queries = []

        def capture(_connection, _cursor, statement, *_args, queries=queries):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        event.listen(c.engine, "before_cursor_execute", capture)
        try:
            result = read(c, search="CAF")
        finally:
            event.remove(c.engine, "before_cursor_execute", capture)
        assert result.total == size
        counts.append(len(queries))
    assert counts[0] == counts[1]
    record_property("record_search_selects_1_and_30", str(counts))


@pytest.mark.parametrize(
    "path",
    [
        "/datasets/a/records",
        "/datasets/a/concept/records",
        "/datasets/a/versions/id/records",
        "/datasets/a/records/",
    ],
)
def test_record_query_values_never_enter_default_access_logs(path):
    record = LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", path + "?q=secret&f0_value=private", "1.1", 200),
        None,
    )
    assert RecordQueryLogFilter().filter(record)
    assert "secret" not in record.getMessage() and "private" not in record.getMessage()
    assert path in record.getMessage() and "200" in record.getMessage()
    malformed = LogRecord("uvicorn.access", 20, "", 0, "unexpected secret", (), None)
    assert not RecordQueryLogFilter().filter(malformed)


@pytest.mark.slow
def test_thousand_record_search_query_plan(context, record_property):
    c = context
    setup(c, populated=False)
    # Canonically validated fixture rows in one transaction. This measures reads,
    # not the unrelated per-Record mutation/audit workflow.
    with c.s.datasets.uow(write=True, serialized=True) as uow:
        fields = uow.dataset_fields.list_for_dataset(c.d.id)
        identifier = uow.dataset_business_keys.get(c.d.id)
        for index in range(1000):
            values, key = c.s.dataset_records._validated_values(
                fields, identifier, {"code": f"perf-{index:04}", "label": "Café"}
            )
            record = uow.dataset_records.create(dataset_id=c.d.id, identifier_key=key)
            uow.flush()
            uow.dataset_records.add_values(record, values)
    statements = []

    def capture(_connection, _cursor, statement, parameters, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    for concept in (True, False):
        if not concept:
            publish(c)
        statements.clear()
        event.listen(c.engine, "before_cursor_execute", capture)
        start = perf_counter()
        try:
            result = c.service.read(c.a.id, c.d.key, prefer_concept=concept, search="CAF")
        finally:
            elapsed = perf_counter() - start
            event.remove(c.engine, "before_cursor_execute", capture)
        assert result.page.total == 1000 and len(result.page.records) == 50
        assert len(statements) < 25
        row_table = "dataset_records" if concept else "dataset_version_records"
        statement, parameters = next(
            (sql, args) for sql, args in statements if f"FROM {row_table} LEFT OUTER JOIN" in sql
        )
        with c.engine.connect() as connection:
            prefix = "EXPLAIN QUERY PLAN " if c.engine.dialect.name == "sqlite" else "EXPLAIN "
            plan = [
                str(tuple(row))
                for row in connection.exec_driver_sql(prefix + statement, parameters)
            ]
        label = "concept" if concept else "current"
        record_property(f"{label}_1000_seconds", round(elapsed, 4))
        record_property(f"{label}_1000_selects", len(statements))
        record_property(f"{label}_query_plan", "\n".join(plan))
