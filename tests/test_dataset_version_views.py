"""REF-83 coherent published reads, route security and immutable exports on both engines."""

import csv
import io
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from test_audit_coverage import audit_db, audits, context, snapshot  # noqa: F401
from test_dataset_versions import NOW, publish, setup

from refrncr.services import NotFoundError
from refrncr.web import SESSION_COOKIE, create_flask_app

HTTPS = "https://localhost"


def configured(c):
    setup(c)
    c.service = c.s.dataset_versions
    c.service.clock = lambda: NOW


def client(c, actor=None):
    actor = actor or c.a
    browser = create_flask_app(
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
    ).test_client()
    token, session = c.s.sessions.create(actor)
    browser.set_cookie(SESSION_COOKIE, token, domain="localhost", secure=True)
    return browser, session.csrf_token


def request(browser, method, path, **kwargs):
    return getattr(browser, method)(path, base_url=HTTPS, **kwargs)


def payload(c, csrf, *, day=None, note="Release"):
    return {
        "csrf_token": csrf,
        "dataset_id": c.d.id,
        "confirmed": "yes",
        "expected_revision": str(c.s.datasets.get_dataset(c.a.id, c.d.key).revision),
        "effective_date": (day or NOW.date()).isoformat(),
        "release_note": note,
    }


def mutate_concept(c):
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Private draft", "Private description")
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Private field", "string", False, False
    )
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Private value"}
    )
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "Private addition"})


def test_no_current_never_exposes_concept(context):  # noqa: F811
    c = context
    configured(c)
    browser, _csrf = client(c, c.u)
    reads = (
        lambda: c.s.datasets.get_dataset(c.u.id, c.d.key),
        lambda: c.s.dataset_fields.list_fields(c.u.id, c.d.key),
        lambda: c.s.dataset_business_keys.get_business_key(c.u.id, c.d.key),
        lambda: c.s.dataset_records.list_records(c.u.id, c.d.key),
        lambda: c.service.read(c.u.id, c.d.key, prefer_concept=True),
    )
    for read in reads:
        with pytest.raises(NotFoundError):
            read()
    assert not c.s.datasets.list_datasets(c.u.id)
    assert not c.s.datasets.list_dataset_overview(c.u.id)
    for suffix in ("", "/records", "/versions", "/fields/code/edit"):
        response = request(browser, "get", f"/datasets/{c.d.key}{suffix}")
        assert response.status_code == 404
        assert c.d.name not in response.text and "Café" not in response.text
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    for version_id in (scheduled.id, str(uuid4())):
        with pytest.raises(NotFoundError):
            c.service.read(c.u.id, c.d.key, version_id)
    assert not c.s.datasets.list_datasets(c.u.id)


def test_current_and_historical_pages_use_only_snapshot_content(context):  # noqa: F811
    c = context
    configured(c)
    version = publish(c)
    mutate_concept(c)
    browser, _csrf = client(c, c.u)
    before = snapshot(c)
    assert c.s.datasets.get_dataset(c.u.id, c.d.key).name == c.d.name
    assert [field.name for field in c.s.dataset_fields.list_fields(c.u.id, c.d.key)] == [
        "Code",
        "Label",
    ]
    assert c.s.dataset_records.list_records(c.u.id, c.d.key).total == 1
    for suffix in ("", "/records", f"/versions/{version.id}", f"/versions/{version.id}/records"):
        response = request(browser, "get", f"/datasets/{c.d.key}{suffix}")
        assert response.status_code == 200
        assert "Private" not in response.text
        assert "Version 1" in response.text and "Read only" in response.text
        assert "Edit field" not in response.text and "Add record" not in response.text
        if suffix.endswith("records"):
            assert "Café" in response.text
        else:
            assert c.d.description in response.text
        assert f"/versions/{version.id}" in response.text
    listing = request(browser, "get", "/datasets")
    assert c.d.name in listing.text and "Private" not in listing.text
    assert snapshot(c) == before
    owner_browser, _ = client(c)
    concept = request(owner_browser, "get", f"/datasets/{c.d.key}/records")
    assert "Private value" in concept.text and "Concept" in concept.text
    historical = request(owner_browser, "get", f"/datasets/{c.d.key}/versions/{version.id}/records")
    assert "Private" not in historical.text and "Edit" not in historical.text


def exported_rows(artifact):
    try:
        if artifact.extension == "json":
            return json.load(artifact.content)
        if artifact.extension == "csv":
            return list(csv.DictReader(io.StringIO(artifact.content.read().decode("utf-8-sig"))))
        workbook = load_workbook(artifact.content, read_only=True, data_only=False)
        try:
            rows = list(workbook.active.values)
            return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]
        finally:
            workbook.close()
    finally:
        artifact.content.close()


@pytest.mark.parametrize("format_name", ["csv", "xlsx", "json"])
@pytest.mark.parametrize("expired", [False, True])
def test_snapshot_exports_all_formats(context, format_name, expired):  # noqa: F811
    c = context
    configured(c)
    first = publish(c)
    mutate_concept(c)
    if expired:
        publish(c, day=NOW.date() + timedelta(days=1))
        c.service.clock = lambda: NOW + timedelta(days=1)
    c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    artifact = c.s.dataset_records.export_dataset(c.u.id, c.d.key, format_name, version_id=first.id)
    assert artifact.download_name == f"{c.d.key}-v1.{format_name}"
    assert exported_rows(artifact) == [{"code": "a", "label": "Café"}]
    event = audits(c)[-1]
    assert event.action == "dataset.export_requested" and event.details == {
        "format": "Excel" if format_name == "xlsx" else format_name.upper()
    }
    browser, _csrf = client(c, c.u)
    response = request(
        browser,
        "get",
        f"/datasets/{c.d.key}/versions/{first.id}/records/export?format={format_name}",
    )
    assert (
        response.status_code == 200
        and f"{c.d.key}-v1.{format_name}" in response.headers["Content-Disposition"]
    )
    response.close()


def test_scheduled_cancelled_visibility_and_export_denial(context):  # noqa: F811
    c = context
    configured(c)
    first = publish(c)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Secret future", "")
    future = NOW.date() + timedelta(days=1)
    scheduled = publish(c, day=future)
    ordinary, _ = client(c, c.u)
    owner, _ = client(c)
    listing = request(ordinary, "get", f"/datasets/{c.d.key}/versions")
    assert listing.status_code == 200
    assert "Version 2" not in listing.text and future.isoformat() not in listing.text
    assert "Secret future" not in listing.text
    assert "Scheduled" in request(owner, "get", f"/datasets/{c.d.key}/versions").text
    assert c.service.read(c.a.id, c.d.key, scheduled.id).context.dataset.name == "Secret future"
    for actor in (c.a, c.u):
        with pytest.raises(NotFoundError):
            c.s.dataset_records.export_dataset(actor.id, c.d.key, "json", version_id=scheduled.id)
    c.service.cancel(c.a.id, c.d.id, scheduled.id)
    for browser in (ordinary, owner):
        for suffix in ("", "/records", "/records/export?format=json"):
            assert (
                request(
                    browser, "get", f"/datasets/{c.d.key}/versions/{scheduled.id}{suffix}"
                ).status_code
                == 404
            )
    assert "Cancelled" in request(owner, "get", f"/datasets/{c.d.key}/versions").text
    assert "Cancelled" not in request(ordinary, "get", f"/datasets/{c.d.key}/versions").text
    with pytest.raises(NotFoundError):
        c.s.dataset_records.export_dataset(c.u.id, c.d.key, "json", version_id=first.id)


def test_publish_form_csrf_confirmation_stale_revision_and_escaping(context):  # noqa: F811
    c = context
    configured(c)
    browser, csrf = client(c)
    path = f"/datasets/{c.d.key}/versions/publish"
    form = request(browser, "get", path)
    assert form.status_code == 200 and "expected_revision" in form.text
    body = payload(c, csrf, note="<script>secret release</script>")
    before = snapshot(c)
    assert request(browser, "post", path, data=body | {"csrf_token": "wrong"}).status_code == 403
    assert request(browser, "post", path, data=body | {"confirmed": "no"}).status_code == 400
    assert snapshot(c) == before
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Changed concept", "")
    stale = request(browser, "post", path, data=body)
    assert stale.status_code == 409 and "Review the current content again" in stale.text
    assert "data-error-focus" in stale.text and "Changed concept" in stale.text
    assert "checked" not in stale.text
    assert not [event for event in audits(c) if event.action == "dataset.version_published"]
    body = payload(c, csrf, note="<script>secret release</script>")
    assert request(browser, "post", path, data=body).status_code == 303
    replay = request(browser, "post", path, data=body)
    assert replay.status_code == 409
    history = request(browser, "get", f"/datasets/{c.d.key}/versions")
    assert "&lt;script&gt;secret release&lt;/script&gt;" in history.text
    assert "<script>secret release" not in history.text
    assert len([event for event in audits(c) if event.action == "dataset.version_published"]) == 1
    assert all("secret release" not in str(event.details) for event in audits(c))


def test_cancel_form_rechecks_activation_and_csrf(context):  # noqa: F811
    c = context
    configured(c)
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    browser, csrf = client(c)
    path = f"/datasets/{c.d.key}/versions/{scheduled.id}/cancel"
    assert request(browser, "get", path).status_code == 200
    before = snapshot(c)
    assert request(browser, "post", path, data={"confirmed": "yes"}).status_code == 403
    assert request(browser, "post", path, data={"csrf_token": csrf}).status_code == 400
    assert snapshot(c) == before
    c.service.clock = lambda: NOW + timedelta(days=1)
    response = request(browser, "post", path, data={"csrf_token": csrf, "confirmed": "yes"})
    assert response.status_code == 409 and "Only a Scheduled version" in response.text
    assert not [event for event in audits(c) if event.action == "dataset.version_cancelled"]
    assert c.service.read(c.u.id, c.d.key).state.status == "Current"


@pytest.mark.parametrize("role", ["owner", "admin", "viewer", "editor", "export", "outsider"])
def test_version_routes_retain_ref73_authority(context, role):  # noqa: F811
    c = context
    configured(c)
    actor = c.a if role == "admin" else c.u
    if role == "owner":
        c.s.memberships.create_membership(c.a.id, actor.id, c.owner.id)
    elif role in {"viewer", "editor"}:
        c.s.grants.create_grant(c.a.id, "user", actor.id, "dataset", c.d.id, role)
    elif role == "export":
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", actor.id)
    browser, csrf = client(c, actor)
    path = f"/datasets/{c.d.key}/versions/publish"
    body = payload(c, csrf, day=NOW.date() + timedelta(days=1))
    if role in {"owner", "admin"}:
        assert request(browser, "get", path).status_code == 200
        assert request(browser, "post", path, data=body).status_code == 303
        version = c.service.history(actor.id, c.d.key).entries[0].version
        cancel_path = f"/datasets/{c.d.key}/versions/{version.id}/cancel"
        assert (
            request(
                browser, "post", cancel_path, data={"csrf_token": csrf, "confirmed": "yes"}
            ).status_code
            == 303
        )
        assert (
            len([event for event in audits(c) if event.action == "dataset.version_cancelled"]) == 1
        )
    else:
        scheduled = publish(c, day=NOW.date() + timedelta(days=1))
        before = snapshot(c)
        assert request(browser, "get", path).status_code == 404
        assert request(browser, "post", path, data=body).status_code == 404
        for version_id in (scheduled.id, str(uuid4())):
            assert (
                request(
                    browser,
                    "post",
                    f"/datasets/{c.d.key}/versions/{version_id}/cancel",
                    data={"csrf_token": csrf, "confirmed": "yes"},
                ).status_code
                == 404
            )
        assert snapshot(c) == before


def test_snapshot_pagination_and_deleted_source_objects(context):  # noqa: F811
    c = context
    configured(c)
    concept_records = [c.record]
    for index in range(51):
        concept_records.append(
            c.s.dataset_records.create_record(
                c.a.id, c.d.key, {"code": f"b{index:02}", "label": f"Snapshot {index}"}
            )
        )
    version = publish(c)
    for record in concept_records:
        c.s.dataset_records.delete_record(c.a.id, c.d.key, record.id, 1)
    c.s.dataset_fields.delete_field(c.a.id, c.d.key, "label")
    replacement = c.s.dataset_fields.create_field(
        c.a.id, c.d.key, "label", "Private replacement", "string"
    )
    first = c.service.read(c.u.id, c.d.key, version.id, page=1)
    second = c.service.read(c.u.id, c.d.key, version.id, page=2)
    assert first.page.total == second.page.total == 52
    assert len(first.page.records) == 50 and len(second.page.records) == 2
    assert first.page.records[0].id == c.record.id
    assert first.fields[1].id != replacement.id and first.fields[1].name == "Label"
    identifiers = [row.id for page in (first.page, second.page) for row in page.records]
    assert len(set(identifiers)) == 52
    assert c.service.read(c.u.id, c.d.key, version.id, page=2).page == second.page
    browser, _ = client(c, c.u)
    path = f"/datasets/{c.d.key}/versions/{version.id}/records"
    response = request(browser, "get", path)
    assert response.status_code == 200
    assert f"{path}?page=2" in response.text
    assert "Café" in response.text and "Private replacement" not in response.text
    assert "View record a" in response.text and "Edit record" not in response.text
    c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    rows = exported_rows(
        c.s.dataset_records.export_dataset(c.u.id, c.d.key, "json", version_id=version.id)
    )
    assert len(rows) == 52 and rows[0] == {"code": "a", "label": "Café"}


def test_history_and_record_reads_have_constant_query_count(context):  # noqa: F811
    from sqlalchemy import event

    c = context
    configured(c)
    publish(c)

    def measured(operation):
        queries = []

        def capture(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        event.listen(c.engine, "before_cursor_execute", capture)
        try:
            result = operation()
        finally:
            event.remove(c.engine, "before_cursor_execute", capture)
        return result, len(queries)

    _, history_queries = measured(lambda: c.service.history(c.u.id, c.d.key))
    _, record_queries = measured(lambda: c.service.read(c.u.id, c.d.key))
    for index in range(5):
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": f"b{index}"})
        publish(c)
    history, grown_history = measured(lambda: c.service.history(c.u.id, c.d.key))
    read, grown_records = measured(lambda: c.service.read(c.u.id, c.d.key))
    assert len(history.entries) == 6 and read.page.total == 6
    assert grown_history == history_queries
    assert grown_records == record_queries
    assert history_queries == 10
    assert record_queries == 14  # Includes current view/maintenance/export capability reads.


@pytest.mark.parametrize("state", ["inactive", "deleted"])
def test_current_permissions_revoke_historical_reads_and_exports(context, state):  # noqa: F811
    c = context
    configured(c)
    version = publish(c)
    access = c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    artifact = c.s.dataset_records.export_dataset(c.u.id, c.d.key, "json", version_id=version.id)
    c.s.datasets.revoke_export_access(c.a.id, c.d.key, access.id)
    # A completed authorized stream remains usable; every new request reauthorizes.
    assert exported_rows(artifact) == [{"code": "a", "label": "Café"}]
    with pytest.raises(NotFoundError, match="^Dataset not found$"):
        c.s.dataset_records.export_dataset(c.u.id, c.d.key, "json", version_id=version.id)
    assert c.service.read(c.u.id, c.d.key, version.id).page.total == 1
    if state == "inactive":
        c.s.users.disable_user(c.a.id, c.u.id)
    else:
        c.s.users.delete_user(c.a.id, c.u.id)
    before = snapshot(c)
    for operation in (
        lambda: c.service.read(c.u.id, c.d.key, version.id),
        lambda: c.service.history(c.u.id, c.d.key),
        lambda: c.s.dataset_records.export_dataset(c.u.id, c.d.key, "json", version_id=version.id),
    ):
        with pytest.raises(NotFoundError, match="^Dataset not found$"):
            operation()
    assert snapshot(c) == before


def test_cross_dataset_and_unknown_version_are_non_disclosing(context):  # noqa: F811
    c = context
    configured(c)
    version = publish(c)
    other = c.s.datasets.create_dataset(c.a.id, "other", "Other", "", c.owner.id)
    browser, _ = client(c)
    before = snapshot(c)
    responses = []
    for key, version_id in (
        (other.key, version.id),
        (c.d.key, str(uuid4())),
        ("missing", version.id),
    ):
        for suffix in ("", "/records", "/records/export?format=json"):
            response = request(browser, "get", f"/datasets/{key}/versions/{version_id}{suffix}")
            assert response.status_code == 404
            responses.append(response.data)
        with pytest.raises(NotFoundError, match="^Dataset not found$"):
            c.service.read(c.a.id, key, version_id)
    assert all(body == responses[0] for body in responses)
    assert snapshot(c) == before


@pytest.mark.parametrize("operation", ["read", "export"])
def test_database_read_failure_is_bounded_and_has_no_success_event(context, monkeypatch, operation):  # noqa: F811
    from sqlalchemy.exc import OperationalError

    from refrncr.ports import PersistenceConflictError
    from refrncr.version_persistence import SqlAlchemyVersionOperations

    c = context
    configured(c)
    version = publish(c)
    browser, _ = client(c)
    before = snapshot(c)

    def fail(*_args, **_kwargs):
        raise OperationalError("SELECT secret_value", {}, RuntimeError("private-database-path"))

    monkeypatch.setattr(SqlAlchemyVersionOperations, "fields", fail)
    with pytest.raises(
        PersistenceConflictError, match="^A persistence constraint rejected the operation$"
    ):
        if operation == "read":
            c.service.read(c.a.id, c.d.key, version.id)
        else:
            c.s.dataset_records.export_dataset(c.a.id, c.d.key, "json", version_id=version.id)
    suffix = "/records" if operation == "read" else "/records/export?format=json"
    response = request(browser, "get", f"/datasets/{c.d.key}/versions/{version.id}{suffix}")
    assert response.status_code == 503
    for private in ("SELECT", "secret_value", "private-database-path", "OperationalError"):
        assert private not in response.text
    assert snapshot(c) == before
