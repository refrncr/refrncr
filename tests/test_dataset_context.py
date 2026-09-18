"""REF-130 working-set aggregates and atomic content freshness."""

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dataset_helpers import create_owned_dataset, downgrade_historical_fixture, publish_dataset
from sqlalchemy import event, select
from test_dataset_browser import body, dataset_browser, login, request  # noqa: F401
from test_dataset_records import (  # noqa: F401
    postgresql_record_services,
    record_services,
)

from refrncr.models import AuditEvent, Dataset
from refrncr.services import RecordConflictError, StaleRecordError, ValidationError

BASE = datetime(2026, 9, 7, 12, 58, tzinfo=UTC)


@pytest.fixture(params=["record_services", "postgresql_record_services"])
def context_services(request):
    return request.getfixturevalue(request.param)


def fresh(services, admin, key="context"):
    dataset = create_owned_dataset(services.datasets, admin.id, key, key, "")
    return dataset


def configure(services, admin, key="context"):
    dataset = fresh(services, admin, key)
    services.dataset_fields.create_field(admin.id, key, "code", "Code", "string", True, False, True)
    return dataset


def stamp(services, admin, key="context"):
    return services.datasets.get_dataset_context(admin.id, key).last_updated


def test_creation_fallback_and_settings_do_not_change_freshness(context_services):
    _, factory, services, admin = context_services
    dataset = fresh(services, admin)
    with factory.begin() as session:
        row = session.get(Dataset, dataset.id)
        row.created_at = BASE
    assert stamp(services, admin) == BASE
    services.datasets.update_dataset(admin.id, dataset.key, "Renamed", "Changed description")
    viewer = services.users.create_user(admin.id, "viewer@example.com", "Viewer", "valid password")
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    # Ownership currently has no mutation service; exercise its persisted metadata only.
    with factory.begin() as session:
        row = session.get(Dataset, dataset.id)
        row.owner_user_id = viewer.id
        row.updated_at = BASE + timedelta(days=1)
    assert stamp(services, admin) == BASE


def test_record_lifecycle_counts_revisions_and_failures(context_services, monkeypatch):
    _, factory, services, admin = context_services
    configure(services, admin)
    previous = stamp(services, admin)
    monkeypatch.setattr("refrncr.services._now", lambda: BASE)
    first = services.dataset_records.create_record(admin.id, "context", {"code": "A"})
    assert stamp(services, admin) == BASE != previous
    overview = services.datasets.list_dataset_overview(admin.id)[0]
    assert overview.no_current and overview.record_count == 0
    assert services.datasets.get_dataset_context(admin.id, "context").record_count == 1
    monkeypatch.setattr("refrncr.services._now", lambda: BASE + timedelta(minutes=1))
    second = services.dataset_records.create_record(admin.id, "context", {"code": "B"})
    assert services.datasets.get_dataset_context(admin.id, "context").record_count == 2
    monkeypatch.setattr("refrncr.services._now", lambda: BASE + timedelta(minutes=2))
    edited = services.dataset_records.update_record(admin.id, "context", first.id, 1, {"code": "C"})
    assert stamp(services, admin) == BASE + timedelta(minutes=2)
    assert edited.revision == 2
    # A revision is optimistic concurrency state, not an extra current record.
    assert services.datasets.get_dataset_context(admin.id, "context").record_count == 2
    with pytest.raises(RecordConflictError):
        services.dataset_records.create_record(admin.id, "context", {"code": "B"})
    with pytest.raises(StaleRecordError):
        services.dataset_records.update_record(admin.id, "context", first.id, 1, {"code": "D"})
    with pytest.raises(StaleRecordError):
        services.dataset_records.delete_record(admin.id, "context", first.id, 1)
    assert stamp(services, admin) == BASE + timedelta(minutes=2)
    monkeypatch.setattr("refrncr.services._now", lambda: BASE + timedelta(minutes=3))
    services.dataset_records.delete_record(admin.id, "context", first.id, 2)
    assert stamp(services, admin) == BASE + timedelta(minutes=3)
    assert services.datasets.get_dataset_context(admin.id, "context").record_count == 1
    services.dataset_records.delete_record(admin.id, "context", second.id, 1)
    assert services.datasets.get_dataset_context(admin.id, "context").record_count == 0
    with factory() as session:
        assert session.scalar(select(AuditEvent.id).where(AuditEvent.object_id == first.id))


@pytest.mark.parametrize("operation", ["create", "edit", "delete", "import", "field", "order"])
def test_commit_failure_rolls_back_timestamp_and_mutation(context_services, monkeypatch, operation):
    _, factory, services, admin = context_services
    dataset = configure(services, admin)
    record = services.dataset_records.create_record(admin.id, dataset.key, {"code": "A"})
    spare = services.dataset_fields.create_field(admin.id, dataset.key, "spare", "Spare", "string")
    original = services.datasets.get_dataset_context(admin.id, dataset.key)
    importer = services.record_imports
    state, _, _ = importer.begin(admin.id, "test", dataset.key, "rows.csv", b"code,ignored\nB,x\n")
    importer.preview(admin.id, "test", dataset.key, state.token, ("code", None))
    fields = services.dataset_fields.list_fields(admin.id, dataset.key)
    order = tuple(field.id for field in fields)

    def fail_commit(session):
        session.flush()  # Ensure the mutation and timestamp have both reached the DB.
        raise RuntimeError("injected commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(factory.class_, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            match operation:
                case "create":
                    services.dataset_records.create_record(admin.id, dataset.key, {"code": "B"})
                case "edit":
                    services.dataset_records.update_record(
                        admin.id, dataset.key, record.id, 1, {"code": "B"}
                    )
                case "delete":
                    services.dataset_records.delete_record(admin.id, dataset.key, record.id, 1)
                case "import":
                    importer.confirm(admin.id, "test", dataset.key, state.token)
                case "field":
                    services.dataset_fields.delete_field(admin.id, dataset.key, spare.key)
                case "order":
                    services.dataset_fields.replace_order(admin.id, dataset.key, order, order[::-1])
    after = services.datasets.get_dataset_context(admin.id, dataset.key)
    assert (after.record_count, after.last_updated) == (
        original.record_count,
        original.last_updated,
    )
    assert services.dataset_records.get_record_for_edit(admin.id, dataset.key, record.id) == record
    assert tuple(f.id for f in services.dataset_fields.list_fields(admin.id, dataset.key)) == order


def test_all_existing_schema_mutations_and_noops(context_services, monkeypatch):
    _, _, services, admin = context_services
    fresh(services, admin)
    moment = BASE
    monkeypatch.setattr("refrncr.services._now", lambda: moment)
    fields = services.dataset_fields
    field = fields.create_field(admin.id, "context", "code", "Code", "string", True)
    assert stamp(services, admin) == moment
    moment += timedelta(minutes=1)
    fields.update_field(admin.id, "context", "code", "Renamed", "string", True, True)
    assert stamp(services, admin) == moment
    moment += timedelta(minutes=1)
    services.dataset_business_keys.set_business_key(admin.id, "context", ("code",))
    assert stamp(services, admin) == moment
    moment += timedelta(minutes=1)
    fields.update_field_and_business_key_membership(
        admin.id, "context", "code", "Renamed", "string", True, True, False
    )
    assert stamp(services, admin) == moment
    spare = fields.create_field(admin.id, "context", "spare", "Spare", "string")
    moment += timedelta(minutes=1)
    fields.move_field(admin.id, "context", spare.id, "up", (field.id, spare.id))
    assert stamp(services, admin) == moment
    moment += timedelta(minutes=1)
    fields.delete_field(admin.id, "context", "spare")
    assert stamp(services, admin) == moment
    previous = moment
    moment += timedelta(minutes=1)
    fields.update_field(admin.id, "context", "code", "Renamed", "string", True, True)
    fields.update_field_and_business_key_membership(
        admin.id, "context", "code", "Renamed", "string", True, True, False
    )
    fields.move_field(admin.id, "context", field.id, "up", (field.id,))
    with pytest.raises(ValidationError):
        fields.update_field(admin.id, "context", "code", "", "string", True, True)
    assert stamp(services, admin) == previous


def test_successful_import_updates_once_and_counts_rows(context_services):
    _, _, services, admin = context_services
    dataset = configure(services, admin)
    before = stamp(services, admin)
    importer = services.record_imports
    state, _, _ = importer.begin(
        admin.id, "test", dataset.key, "rows.csv", b"code,ignored\nA,x\nB,x\n"
    )
    importer.preview(admin.id, "test", dataset.key, state.token, ("code", None))
    assert stamp(services, admin) == before
    assert importer.confirm(admin.id, "test", dataset.key, state.token) == 2
    context = services.datasets.get_dataset_context(admin.id, dataset.key)
    assert context.last_updated > before
    assert context.record_count == 2


def test_bulk_query_count_and_authorization(context_services, monkeypatch):
    engine, _, services, admin = context_services
    dataset = configure(services, admin)
    viewer = services.users.create_user(admin.id, "viewer@example.com", "Viewer", "valid password")
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    queries = []

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        queries.append((statement, parameters))

    def measured(actor):
        queries.clear()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            result = services.datasets.list_dataset_overview(actor.id)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        return result, len(queries)

    _, initial_queries = measured(admin)
    published = [dataset]
    for index in range(12):
        published.append(fresh(services, admin, f"hidden-{index}"))
    rows, grown_queries = measured(admin)
    assert len(rows) == 13
    assert all(row.record_count == 0 for row in rows)
    assert grown_queries == initial_queries
    for item in published:
        publish_dataset(services, admin.id, item)
    rows, _ = measured(viewer)
    assert len(rows) == 13
    aggregates = [(sql, params) for sql, params in queries if "count(dataset_records.id)" in sql]
    assert len(aggregates) == 1
    sql, params = aggregates[0]
    assert "WHERE dataset_records.dataset_id IN" not in sql
    assert "WHERE datasets.id IN" not in sql
    assert services.datasets.get_dataset_context(viewer.id, "hidden-0").record_count == 0
    outsider = services.users.create_user(admin.id, "none@example.com", "None", "valid password")
    rows, _ = measured(outsider)
    assert len(rows) == 13
    assert any("count(dataset_records.id)" in sql for sql, _ in queries)


def test_overview_context_breadcrumbs_and_utc(dataset_browser):  # noqa: F811
    factory, services, admin, client = dataset_browser
    dataset = configure(services, admin)
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "A"})
    with factory.begin() as session:
        row = session.get(Dataset, dataset.id)
        row.name = "<script>unsafe & name</script>"
        row.content_updated_at = BASE
    login(client)
    page = body(request(client, "get", "/datasets"))
    assert "Search datasets" not in page
    assert "<th scope='col'>Dataset</th>" in page
    assert "<th scope='col' class='numeric-column'>Records</th>" in page
    assert "<th scope='col'>Updated</th>" in page
    assert "<td class='numeric-column'>—</td>" in page
    assert "<th scope='col'>Version</th>" in page
    assert "href='/datasets/context/versions'>Concept in progress</a>" in page
    assert "No current version" not in page
    assert "7 Sep 2026, 12:58 UTC" not in page
    for path in (
        "/datasets/context/concept",
        "/datasets/context/concept/records",
        "/datasets/context/edit",
        "/datasets/context/records/import",
    ):
        if path.endswith("/import"):
            blocked = request(client, "get", path)
            assert blocked.status_code == 400
            assert "Records in this Concept have already been changed" in body(blocked)
            publish_dataset(services, admin.id, dataset)
            services.dataset_versions.create_concept(admin.id, dataset.id)
        response = request(client, "get", path)
        assert response.status_code == 200
        html = body(response)
        breadcrumb = html.split('<nav class="breadcrumbs"')[1].split("</nav>")[0]
        assert "<ol>" in breadcrumb
        assert '<a href="/datasets">Datasets</a>' in breadcrumb
        assert "<span>Test data domain</span>" in breadcrumb
        assert (
            '<span aria-current="page">&lt;script&gt;unsafe &amp; name&lt;/script&gt;</span>'
            in breadcrumb
        )
        assert breadcrumb.count("<a ") == 1
        assert "<script>unsafe" not in html
        if path in ("/datasets/context/concept", "/datasets/context/concept/records"):
            assert re.search(
                r'<div class="page-context">'
                r'\s*<nav class="breadcrumbs".*?</nav>\s*<h1 class="page-title">.*?</h1>'
                r'\s*<p class="metadata">.*?</p>\s*</div>',
                html,
                re.DOTALL,
            )
            assert "1 record</span>" in html
            metadata = html.split('<p class="metadata">', 1)[1].split("</p>", 1)[0]
            assert metadata.count('class="metadata-item"') == 1
            assert '<span aria-hidden="true">•</span><span>Updated ' in metadata
            assert re.search(r'</span>\s+<span class="metadata-item">', metadata)
            assert "&nbsp;" not in metadata
            assert " • " not in metadata
            assert "<p" not in metadata
            assert "page-context-heading" not in html
            assert (
                'Updated <time datetime="2026-09-07T12:58:00+00:00">7 Sep 2026, 12:58 UTC' in html
            )
    formatter = client.application.jinja_env.filters["absolute_utc"]
    assert formatter(BASE.astimezone(timezone(timedelta(hours=3)))) == "7 Sep 2026, 12:58 UTC"
    css = Path("src/refrncr/static/css/components/tables.css").read_text()
    assert ".numeric-column { text-align: right; font-variant-numeric: tabular-nums; }" in css


def test_ref_130_migration_backfill_and_downgrade_preserve_data(context_services):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    engine, factory, services, admin = context_services
    dataset = configure(services, admin)
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "A"})
    untouched = fresh(services, admin, "empty")
    # REF-83 requires even a zero-Record Dataset to have a publishable schema.
    services.dataset_fields.create_field(
        admin.id, untouched.key, "code", "Code", "string", True, True, True
    )
    with factory.begin() as session:
        for key in (dataset.id, untouched.id):
            row = session.get(Dataset, key)
            row.created_at = BASE
            row.updated_at = BASE + timedelta(days=1)
    config = Config("alembic.ini")
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.stamp(config, "head")
        connection.commit()
        downgrade_historical_fixture(config, "0010_ref_121")
        connection.commit()
        assert "content_updated_at" not in {
            c["name"] for c in inspect(connection).get_columns("datasets")
        }
        tables = (
            "datasets",
            "dataset_fields",
            "dataset_records",
            "dataset_record_values",
            "dataset_business_key_fields",
            "grants",
            "audit_events",
        )
        before = {
            table: connection.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all()
            for table in tables
        }
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert (
            connection.execute(
                text("SELECT count(*) FROM datasets WHERE content_updated_at = created_at")
            ).scalar_one()
            == 2
        )
        connection.commit()
        downgrade_historical_fixture(config, "0010_ref_121")
        connection.commit()
        after = {
            table: connection.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all()
            for table in tables
        }
        assert after == before
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
    with factory() as session:
        stored = session.get(Dataset, dataset.id)
        assert stored.content_updated_at.replace(tzinfo=UTC) == BASE
    assert services.datasets.get_dataset_context(admin.id, "context").record_count == 1


def test_shared_page_context_spacing_keeps_outer_header_layout():
    css = Path("src/refrncr/static/css/layouts/page.css").read_text()

    def declarations(selector):
        rule = re.search("^" + re.escape(selector) + r" \{([^}]+)\}", css, re.MULTILINE)
        assert rule is not None
        return dict(part.strip().split(":", 1) for part in rule[1].split(";") if part.strip())

    wrappers = declarations(".page-context")
    assert wrappers["display"].strip() == "flex"
    assert wrappers["flex-direction"].strip() == "column"
    assert wrappers["min-width"].strip() == "0"
    assert wrappers["gap"].strip() == "0"
    assert (
        declarations(".page-context > .metadata")["padding-block-start"].strip()
        == "var(--space-sm)"
    )
    assert (
        declarations(".page-context > .breadcrumbs")["padding-block-end"].strip()
        == "var(--space-sm)"
    )
    reset = declarations(
        ".page-context > .breadcrumbs, .page-context > .page-title, .page-context > .metadata"
    )
    assert reset["margin"].strip() == "0"
    header = declarations(".page-header")
    assert header["flex-wrap"].strip() == "wrap"
    assert header["margin-bottom"].strip() == "var(--space-lg)"

    breadcrumb = declarations(".breadcrumbs")
    assert breadcrumb["text-transform"].strip() == "uppercase"
    assert breadcrumb["font-weight"].strip() == "var(--font-weight-regular)"
    assert breadcrumb["font-size"].strip() == "var(--font-size-label)"
    assert breadcrumb["letter-spacing"].strip() == "var(--letter-spacing-label)"
    assert breadcrumb["line-height"].strip() == "normal"
    assert breadcrumb["color"].strip() == "var(--color-text-muted)"
    metadata = declarations(".metadata")
    assert metadata["display"].strip() == "inline-flex"
    assert metadata["font-size"].strip() == "var(--font-size-body-sm)"
    assert metadata["line-height"].strip() == "normal"
    assert metadata["flex-wrap"].strip() == "wrap"
    assert metadata["gap"].strip() == "var(--space-xs) var(--space-sm)"
    assert metadata["font-variant-numeric"].strip() == "tabular-nums"
    assert declarations(".metadata-item")["display"].strip() == "inline-flex"
    base = Path("src/refrncr/static/css/base.css").read_text()
    assert "h1, .page-title {" in base
    assert (
        ".page-title { font-size: var(--font-size-page-title); "
        "font-weight: var(--font-weight-bold); line-height: normal; letter-spacing: -.01em; }"
        in base
    )
    assert "font-family: var(--font-display)" in base
    for name in ("detail", "records"):
        template = Path(f"src/refrncr/templates/pages/datasets/{name}.html").read_text()
        assert 'from "layouts/page.html" import page_context' in template
        assert "{% call page_context(" in template
