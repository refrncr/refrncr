"""REF-162 focused metadata, navigation, governance and membership contracts."""

import json
import re
import subprocess
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from dataset_helpers import publish_dataset
from sqlalchemy import event, inspect, text
from test_audit_coverage import audit_db, context, snapshot  # noqa: F401
from test_dataset_browser import (  # noqa: F401
    body,
    create_dataset,
    dataset_browser,
    login,
    request,
    session_csrf,
)
from test_dataset_version_migration import config_for
from test_dataset_versions import NOW, content, publish, setup

from refrncr.dataset_diff import DatasetDiffService
from refrncr.presentation_metadata import DATASET_ICONS, key_from_name
from refrncr.services import AuthorizationError, NotFoundError, ValidationError

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Café déjà vu", "cafe_deja_vu"),
        ("  Sales / EUR -- total... ", "sales_eur_total"),
        ("A___B", "a_b"),
        ("Crème\tbrûlée\nkg", "creme_brulee_kg"),
        ("é e\u0301", "e_e"),
        ("<img>@!#😀", "img"),
        ("東京", ""),
        ("１２３ Ｋ", "123_k"),
        ("a—b\\c", "a_b_c"),
        ("İstanbul", "istanbul"),
        ("Name" * 40, "name" * 40),
    ],
)
def test_key_canonicalization_matches_javascript(name, expected):
    assert key_from_name(name) == expected
    result = subprocess.run(
        [
            "node",
            "-e",
            "global.window={addEventListener(){}};"
            "global.document={querySelectorAll(){return []}};"
            "require(process.argv[1]);"
            "process.stdout.write(window.refrncrKeyFromName(JSON.parse(process.argv[2])));",
            str(ROOT / "src/refrncr/static/js/key-generation.js"),
            json.dumps(name),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == expected


@pytest.mark.parametrize(
    "icon", [*DATASET_ICONS, "table", "warehouse", "align-horizontal-distribute-center", "", None]
)
def test_icon_catalog_and_clear(context, icon):  # noqa: F811
    c = context
    d = c.s.datasets.create_dataset(c.a.id, "icon", "Icon", "", c.owner.id, icon=icon)
    assert d.icon == (icon or None)
    c.s.datasets.update_dataset(c.a.id, d.key, "Icon", "", icon="table")
    assert c.s.datasets.get_dataset_for_edit(c.a.id, d.key).icon == "table"
    c.s.datasets.update_dataset(c.a.id, d.key, "Icon", "", icon="")
    assert c.s.datasets.get_dataset_for_edit(c.a.id, d.key).icon is None


@pytest.mark.parametrize(
    "icon",
    [
        "<svg onload=alert(1)>",
        "https://x",
        "../icons",
        12,
        "x" * 65,
        "unknown",
        "table/../../etc/passwd",
        "table\x00",
        "TABLE" + " " * 60,
        "💥",
        {},
        ["table"],
    ],
)
def test_untrusted_icon_input_safely_defaults(context, icon):  # noqa: F811
    from refrncr.presentation_metadata import rendered_dataset_icon

    c = context
    d = c.s.datasets.create_dataset(c.a.id, "fallback", "Fallback", "", c.owner.id, icon=icon)
    assert d.icon is None
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Saved metadata", "Saved description", icon=icon)
    saved = c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key)
    assert (saved.name, saved.description, saved.icon) == (
        "Saved metadata",
        "Saved description",
        None,
    )
    assert rendered_dataset_icon(icon) == "table"


def test_field_metadata_full_lifecycle(context):  # noqa: F811
    c = context
    setup(c)
    c.s.dataset_fields.update_field(
        c.a.id,
        c.d.key,
        "label",
        "Label",
        "string",
        False,
        False,
        definition="Meaning <b>plain</b>",
        unit="EUR",
    )
    first = publish(c)
    saved = content(c, first)
    assert saved.fields[1].definition == "Meaning <b>plain</b>"
    assert saved.fields[1].unit == "EUR"
    c.service.create_concept(c.a.id, c.d.id)
    field = c.s.dataset_fields.get_field(c.a.id, c.d.key, "label")
    assert (field.definition, field.unit) == (saved.fields[1].definition, "EUR")
    c.s.dataset_fields.update_field_and_business_key_membership(
        c.a.id,
        c.d.key,
        "label",
        "Label",
        "string",
        False,
        False,
        False,
        definition="New definition",
        unit="kg",
    )
    diff = DatasetDiffService(c.service)
    review = diff.open_review(c.a.id, c.d.id)
    assert review.counts.fields_changed == 1
    assert review.counts.records_changed == 0
    detail = diff.field_detail(c.a.id, review.identity, field.id)
    assert {p.name for p in detail.properties} == {"Definition", "Unit"}
    second = publish(c, day=NOW.date() + timedelta(days=1))
    assert content(c, first) == saved
    assert content(c, second).fields[1].unit == "kg"
    assert content(c, second).records == saved.records
    c.service.create_concept(c.a.id, c.d.id)
    assert c.s.dataset_fields.get_field(c.a.id, c.d.key, "label").unit == "kg"
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Label", "string", False, False, definition="", unit=""
    )
    cleared = c.s.dataset_fields.get_field(c.a.id, c.d.key, "label")
    assert cleared.definition is None and cleared.unit is None


@pytest.mark.parametrize(
    ("definition", "unit"), [("x" * 4001, ""), ("", "x" * 65), (123, ""), ("", [])]
)
def test_field_metadata_limits_and_rollback(context, definition, unit):  # noqa: F811
    c = context
    before = snapshot(c)
    with pytest.raises(ValidationError):
        c.s.dataset_fields.create_field(
            c.a.id, c.d.key, "field", "Field", "string", definition=definition, unit=unit
        )
    assert snapshot(c) == before


def test_field_metadata_boundary_noop_and_denial(context):  # noqa: F811
    c = context
    field = c.s.dataset_fields.create_field(
        c.a.id, c.d.key, "field", "Field", "string", definition="x" * 4000, unit="x" * 64
    )
    before = snapshot(c)
    c.s.dataset_fields.update_field(
        c.a.id,
        c.d.key,
        field.key,
        field.name,
        "string",
        False,
        False,
        definition=field.definition,
        unit=field.unit,
    )
    assert snapshot(c) == before
    with pytest.raises(NotFoundError):
        c.s.dataset_fields.update_field(
            c.u.id,
            c.d.key,
            field.key,
            field.name,
            "string",
            False,
            False,
            definition="Secret",
            unit="kg",
        )
    assert snapshot(c) == before


@contextmanager
def queries(engine):
    result = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            result.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield result
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_relationship_queries_and_authorization(context, record_property):  # noqa: F811
    c = context
    with queries(c.engine) as empty:
        assert c.s.memberships.list_relationships(c.a.id) == ()
    c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    with queries(c.engine) as single:
        assert len(c.s.memberships.list_relationships(c.a.id)) == 1
    for i in range(5):
        user = c.s.users.create_user(
            c.a.id, f"member{i}@example.com", f"Member {i}", "valid password"
        )
        for team in (c.owner, c.other):
            c.s.memberships.create_membership(c.a.id, user.id, team.id)
    with queries(c.engine) as many:
        rows = c.s.memberships.list_relationships(c.a.id)
    assert len(rows) == 11
    assert len(empty) == len(single) == len(many) == 2
    record_property(
        "relationship_service_selects", json.dumps([len(empty), len(single), len(many)])
    )
    assert [(r.team_name.casefold(), r.user_name.casefold()) for r in rows] == sorted(
        (r.team_name.casefold(), r.user_name.casefold()) for r in rows
    )
    with pytest.raises(AuthorizationError):
        c.s.memberships.list_relationships(c.u.id)


def test_configure_inline_access_and_small_edit_link(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "polish", "Polish")
    login(client)
    html = body(request(client, "get", f"/datasets/{d.key}/concept"))
    assert "id='access-management'" in html
    assert "href='#access-management'>Access management</a>" in html
    edit = re.search(
        r"<a[^>]+href='/datasets/polish/configure\?edit=1#dataset-name'[^>]*>.*?Edit dataset</a>",
        html,
        re.DOTALL,
    ).group()
    assert "button-secondary" in edit and "data-panel-url" not in edit
    assert len(re.findall(r"id='configuration-panel'", html)) == 1
    target = s.users.create_user(a.id, "export@example.com", "Exporter", "valid password")
    csrf = session_csrf(client, s)
    result = request(
        client,
        "post",
        "/datasets/polish/export-access?inline=1&concept=1",
        data={"csrf_token": csrf, "subject": f"user:{target.id}"},
    )
    assert result.status_code == 302 and result.location.endswith("/concept#access-management")
    html = body(request(client, "get", result.location))
    assert "Remove export access for Exporter" in html
    ids = re.findall(r"\bid=[\'\"]([^\'\"]+)", html)
    assert len(ids) == len(set(ids))
    entry = s.datasets.export_access_context(a.id, d.key)[3][0][0]
    result = request(
        client,
        "post",
        f"/datasets/polish/export-access/{entry.id}/revoke?inline=1&concept=1",
        data={"csrf_token": csrf},
    )
    assert result.status_code == 302
    assert not s.datasets.export_access_context(a.id, d.key)[3]


def test_dataset_list_selects_concept_without_current(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "polish", "Polish")
    login(client)
    html = body(request(client, "get", "/datasets"))
    assert "href='/datasets/polish/concept/records'" in html
    version_cell = re.search(r"<td>\s*<a class='badge badge-draft badge-link'.*?</td>", html)
    assert version_cell and "title='Concept in progress'>Concept</a>" in version_cell.group()
    assert "title='configuration'" in html
    assert "href='/datasets/polish/configure'" in html
    assert request(client, "get", "/datasets/polish/concept/records").status_code == 200
    assert request(client, "get", "/datasets/missing/concept/records").status_code == 404
    assert d.key == "polish"


def test_logos_and_generated_key_edit_boundary(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    html = body(request(client, "get", "/login"))
    assert 'class="auth-loader"' in html and "refrncr-loader.svg" in html
    assert 'class="auth-wordmark"' in html and "refrncr-wordmark-primary.svg" in html
    assert "images/favicon.svg" in html
    login(client)
    d = create_dataset(s, a, "polish", "Polish")
    assert "data-generate-key" in body(request(client, "get", "/datasets/new"))
    assert "data-generate-key" not in body(request(client, "get", f"/datasets/{d.key}/edit"))
    assets = ROOT / "src/refrncr/static/images"
    assert {p.name for p in assets.iterdir()} == {
        "favicon.svg",
        "lucide-1.46.0.svg",
        "lucide-legacy-0.468.0.svg",
        "refrncr-loader.svg",
        "refrncr-wordmark-primary.svg",
    }


def test_metadata_upgrade_preserves_existing_content(context):  # noqa: F811
    c = context
    setup(c)
    publish(c)
    with c.engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "0021_ref_40")
        command.downgrade(config, "0019_ref_162")
        command.downgrade(config, "0018_ref_160")
        old = connection.execute(text("SELECT id, name, description, revision FROM datasets")).all()
        fields = connection.execute(
            text("SELECT version_id, id, key, name FROM dataset_version_fields ORDER BY id")
        ).all()
        connection.commit()
        command.upgrade(config, "head")
        assert (
            connection.execute(text("SELECT id, name, description, revision FROM datasets")).all()
            == old
        )
        assert (
            connection.execute(
                text("SELECT version_id, id, key, name FROM dataset_version_fields ORDER BY id")
            ).all()
            == fields
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM dataset_version_fields "
                    "WHERE definition IS NOT NULL OR unit IS NOT NULL"
                )
            )
            == 0
        )
        assert {"definition", "unit"} <= {
            c["name"] for c in inspect(connection).get_columns("dataset_fields")
        }
        connection.commit()
        command.upgrade(config, "head")
        assert connection.scalar(text("SELECT count(*) FROM dataset_versions")) == 1


def test_relationship_http_query_counts_and_hidden_rosters(dataset_browser, record_property):  # noqa: F811
    factory, s, a, client = dataset_browser
    login(client)
    with factory() as session:
        engine = session.get_bind()
    with queries(engine) as user_empty:
        assert request(client, "get", "/users").status_code == 200
    with queries(engine) as team_empty:
        assert request(client, "get", "/teams").status_code == 200
    for i in range(6):
        user = s.users.create_user(a.id, f"roster{i}@example.com", f"Roster {i}", "valid password")
        team = s.teams.create_team(a.id, f"Roster team {i}")
        s.memberships.create_membership(a.id, user.id, team.id)
        s.memberships.create_membership(a.id, a.id, team.id)
    with queries(engine) as user_many:
        html = body(request(client, "get", "/users"))
    with queries(engine) as team_many:
        team_html = body(request(client, "get", "/teams"))
    assert len(user_empty) == len(user_many)
    assert len(team_empty) == len(team_many)
    record_property(
        "relationship_http_selects",
        json.dumps(
            {"users": [len(user_empty), len(user_many)], "teams": [len(team_empty), len(team_many)]}
        ),
    )
    assert "Roster team 5" in html and "Roster 5" in team_html
    reader = client.application.test_client()
    login(reader, "roster0@example.com", "valid password")
    readonly = body(request(reader, "get", "/teams"))
    assert "Roster team 0" in readonly
    assert "Roster team 1" not in readonly and "Roster 1" not in readonly
    assert request(reader, "get", "/users").status_code == 403


def test_current_routing_and_inline_access_denial(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "published", "Published")
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, True, True)
    publish_dataset(s, a.id, d)
    login(client)
    assert "href='/datasets/published/records'" in body(request(client, "get", "/datasets"))
    s.dataset_versions.create_concept(a.id, d.id)
    assert "href='/datasets/published/records'" in body(request(client, "get", "/datasets"))
    user = s.users.create_user(a.id, "readonly@example.com", "Read only", "valid password")
    reader = client.application.test_client()
    login(reader, user.email, "valid password")
    html = body(request(reader, "get", "/datasets"))
    assert "Concept in progress" not in html
    assert request(reader, "get", "/datasets/published/concept/records").status_code == 404
    assert "access-management-heading" not in body(request(reader, "get", "/datasets/published"))
    csrf = session_csrf(reader, s)
    denied = request(
        reader,
        "post",
        "/datasets/published/export-access?inline=1",
        data={"csrf_token": csrf, "subject": f"user:{user.id}"},
    )
    assert denied.status_code == 404
    no_csrf = request(
        client,
        "post",
        "/datasets/published/export-access?inline=1",
        data={"subject": f"user:{user.id}"},
    )
    assert no_csrf.status_code == 403


def test_field_metadata_retained_deletion_and_restore(context):  # noqa: F811
    c = context
    setup(c)
    c.s.dataset_fields.update_field(
        c.a.id,
        c.d.key,
        "label",
        "Label",
        "string",
        False,
        False,
        definition="Retained definition",
        unit="kg",
    )
    c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
    with c.s.datasets.uow() as uow:
        deleted = list(uow.dataset_records.deleted_records(c.d.id))
        assert len(deleted) == 1
        record, fields = deleted[0]
        label = next(f for f in fields if f.key == "label")
        assert (label.definition, label.unit) == ("Retained definition", "kg")
        concept = uow.concepts.get(c.d.id)
    restored = c.s.dataset_records.restore_record(
        c.a.id, c.d.key, record.id, record.revision, concept_id=concept.id
    )
    assert restored.values == c.record.values


@pytest.mark.parametrize("value", ["before\x00after", "\x00"])
def test_null_metadata_is_rejected_on_both_engines(context, value):  # noqa: F811
    c = context
    with pytest.raises(ValidationError, match="null characters"):
        c.s.dataset_fields.create_field(c.a.id, c.d.key, "null", "Null", "string", definition=value)


def test_discard_restores_field_metadata_and_preserves_current_icon(context):  # noqa: F811
    c = context
    setup(c)
    c.s.dataset_fields.update_field(
        c.a.id,
        c.d.key,
        "label",
        "Label",
        "string",
        False,
        False,
        definition="Published meaning",
        unit="EUR",
    )
    first = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    before_icon = c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).revision
    c.s.datasets.update_dataset(c.a.id, c.d.key, c.d.name, c.d.description, icon="house")
    assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).revision == before_icon
    c.s.dataset_fields.update_field(
        c.a.id,
        c.d.key,
        "label",
        "Label",
        "string",
        False,
        False,
        definition="Concept meaning",
        unit="kg",
    )
    review = c.service.review_discard(c.a.id, c.d.id)
    c.service.discard_concept(c.a.id, c.d.id, review.identity)
    c.service.create_concept(c.a.id, c.d.id)
    field = c.s.dataset_fields.get_field(c.a.id, c.d.key, "label")
    assert (field.definition, field.unit) == ("Published meaning", "EUR")
    assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).icon == "house"
    assert content(c, first).fields[1].definition == "Published meaning"


@pytest.mark.parametrize(("submitted", "expected"), [("unknown", None), (" TABLE ", "table")])
def test_manual_icon_normalization_and_fallback(context, submitted, expected):  # noqa: F811
    c = context
    c.s.datasets.update_dataset(c.a.id, c.d.key, c.d.name, c.d.description, icon=submitted)
    assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).icon == expected


def test_manual_icon_rendering_and_legacy_fallback(dataset_browser):  # noqa: F811
    from refrncr.presentation_metadata import rendered_dataset_icon

    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "manual-icon", "Manual icon")
    s.datasets.update_dataset(a.id, d.key, d.name, d.description, icon="database")
    login(client)
    assert "lucide-1.46.0.svg#database" in body(request(client, "get", "/datasets"))
    assert "value='database'" in body(request(client, "get", f"/datasets/{d.key}/edit"))
    assert rendered_dataset_icon("missing") == "table"
    assert rendered_dataset_icon("<svg>") == "table"
    assert rendered_dataset_icon(None) == "table"


def test_long_icon_migration_preserves_values_and_refuses_lossy_downgrade(context):  # noqa: F811
    c = context
    name = "align-horizontal-distribute-center"
    c.s.datasets.update_dataset(c.a.id, c.d.key, c.d.name, c.d.description, icon=name)
    with c.engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        with pytest.raises(RuntimeError, match="exceed 32"):
            command.downgrade(config, "0019_ref_162")
    assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).icon == name
    c.s.datasets.update_dataset(c.a.id, c.d.key, c.d.name, c.d.description, icon="database")
    with c.engine.connect() as connection:
        config = config_for(connection)
        command.downgrade(config, "0019_ref_162")
        command.upgrade(config, "head")
    assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).icon == "database"


def test_configure_inline_edit_and_cancel_preserve_content(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "inline-edit", "Original")
    login(client)
    display = body(request(client, "get", "/datasets/inline-edit/concept"))
    assert "Save dataset" not in display
    assert "Delete dataset" in display
    assert display.index("fields-heading") < display.index("dataset-danger-heading")
    editing = body(request(client, "get", "/datasets/inline-edit/concept?edit=1"))
    assert "id='dataset-edit-form'" in editing
    assert "Save dataset" in editing and ">Cancel</a>" in editing
    assert "data-side-panel-content" not in editing
    assert "<dt>Technical name</dt>" not in editing
    assert "name='technical_key'" in editing and "value='inline-edit'" in editing
    assert "name='key'" not in editing
    assert "name='owner_team_id'" in editing and "name='icon'" in editing
    csrf = session_csrf(client, s)
    invalid = request(
        client,
        "post",
        "/datasets/inline-edit",
        data={"csrf_token": csrf, "name": "", "description": "Draft text", "icon": "cloud"},
    )
    assert invalid.status_code == 400
    html = body(invalid)
    assert "id='dataset-edit-form'" in html and "fields-heading" in html
    assert ">Draft text</textarea>" in html and "value='cloud'" in html
    assert "data-error-focus" in html
    request(client, "get", "/datasets/inline-edit/concept")
    assert s.datasets.get_dataset_for_edit(a.id, d.key).name == "Original"
    reader = client.application.test_client()
    user = s.users.create_user(a.id, "inline-reader@example.com", "Reader", "valid password")
    login(reader, user.email, "valid password")
    assert request(reader, "get", "/datasets/inline-edit/concept?edit=1").status_code == 404
    assert request(client, "post", "/datasets/inline-edit", data={"name": "Bad"}).status_code == 403


def test_configure_section_links_respect_existing_permissions(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "tabs", "Tabs")
    login(client)
    html = body(request(client, "get", "/datasets/tabs/concept"))
    nav = re.search(r"<nav[^>]*data-section-tabs>(.*?)</nav>", html, re.S).group(1)
    assert re.findall(r"href='#([^']+)'", nav) == [
        "dataset-details",
        "fields",
        "access-management",
        "danger-zone",
    ]
    for target in ["dataset-details", "fields", "access-management", "danger-zone"]:
        assert f"id='{target}'" in html
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, True, True)
    publish_dataset(s, a.id, d)
    user = s.users.create_user(a.id, "tabs-reader@example.com", "Reader", "valid password")
    reader = client.application.test_client()
    login(reader, user.email, "valid password")
    html = body(request(reader, "get", "/datasets/tabs"))
    nav = re.search(r"<nav[^>]*data-section-tabs>(.*?)</nav>", html, re.S).group(1)
    assert re.findall(r"href='#([^']+)'", nav) == ["dataset-details", "fields"]
    assert "id='access-management'" not in html and "id='danger-zone'" not in html


@pytest.mark.parametrize("with_concept", [False, True])
def test_dataset_metadata_is_independent_of_versions(context, with_concept):  # noqa: F811
    from refrncr.services import ConflictError

    c = context
    setup(c)
    first = publish(c)
    original = content(c, first)
    if with_concept:
        c.service.create_concept(c.a.id, c.d.id)
        identity = c.service.review_discard(c.a.id, c.d.id).identity
    revision = c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).revision
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Live name", "Live description", icon="database")
    assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).revision == revision
    assert content(c, first) == original
    assert c.service.read(c.a.id, c.d.key).context.dataset.name == "Live name"
    if with_concept:
        assert not c.service.concept_context(c.a.id, c.d.key).has_changes
        c.service.discard_concept(c.a.id, c.d.id, identity)
    c.service.create_concept(c.a.id, c.d.id)
    actual = c.s.datasets.get_dataset_configuration(c.a.id, c.d.key).dataset
    assert (actual.name, actual.description, actual.icon) == (
        "Live name",
        "Live description",
        "database",
    )
    assert not c.service.concept_context(c.a.id, c.d.key).has_changes
    with pytest.raises(ConflictError, match="matches"):
        publish(c)
    assert content(c, first) == original


def test_danger_zone_shows_what_deletion_removes(dataset_browser):  # noqa: F811
    """The tab states the destructive action and counts everything it takes with it."""
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "danger", "Danger")
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, True, True)
    publish_dataset(s, a.id, d)
    login(client)
    html = body(request(client, "get", "/datasets/danger/configure"))
    danger = html.split("<section id='danger-zone'", 1)[1]
    assert "Delete this dataset" in danger and "cannot be undone" in danger
    for label, count in [("Versions", 1), ("Records", 0), ("Fields", 1), ("Export access", 0)]:
        assert f"{label} <b>{count}</b>" in danger
    assert "The audit log keeps a record of the deletion" in danger

    # The confirmation panel repeats the counts and asks for the exact name.
    panel = body(request(client, "get", f"/datasets/id/{d.id}/delete"))
    assert "<dt>Versions</dt><dd>1</dd>" in panel
    assert "<dt>Export access</dt><dd>0</dd>" in panel
    assert "Type <code>Danger</code> to confirm" in panel
    assert "Audit history is retained." in panel


def test_standalone_configuration_without_concept(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "standalone", "Standalone")
    publish_dataset(s, a.id, d)
    login(client)
    html = body(request(client, "get", "/datasets/standalone/configure"))
    assert "href='#fields'>Fields</a>" in html
    assert "id='fields' aria-labelledby='fields-heading' hidden" not in html
    assert "Current · Version 1 · Read only" in html
    assert "id='access-management'" in html and "id='danger-zone'" in html
    assert "You’re working on a concept" not in html
    csrf = session_csrf(client, s)
    response = request(
        client,
        "post",
        "/datasets/standalone",
        data={
            "csrf_token": csrf,
            "name": "Current details",
            "description": "Independent",
            "icon": "database",
        },
    )
    assert response.status_code == 302 and response.location.endswith("/configure")
    assert "Current details" in body(request(client, "get", response.location))
    assert (
        request(client, "post", "/datasets/standalone", data={"name": "No CSRF"}).status_code == 403
    )
    assert request(client, "get", "/datasets/missing/configure").status_code == 404


@pytest.mark.parametrize("published", [False, True])
def test_standalone_configuration_preserves_read_and_write_authority(dataset_browser, published):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "authority", "Authority")
    if published:
        publish_dataset(s, a.id, d)
    user = s.users.create_user(a.id, "reader-config@example.com", "Reader", "valid password")
    login(client, user.email, "valid password")
    response = request(client, "get", "/datasets/authority/configure")
    assert response.status_code == (200 if published else 404)
    assert "Edit dataset" not in response.text
    assert "id='access-management'" not in response.text
    assert "id='danger-zone'" not in response.text
    assert request(client, "get", "/datasets/authority/configure?edit=1").status_code == 404
    before = s.datasets.get_dataset_for_edit(a.id, d.key)
    before = {column.name: getattr(before, column.name) for column in before.__table__.columns}
    denied = request(
        client,
        "post",
        "/datasets/authority",
        data={"csrf_token": session_csrf(client, s), "name": "Forged", "description": ""},
    )
    assert denied.status_code == 404
    after = s.datasets.get_dataset_for_edit(a.id, d.key)
    assert {
        column.name: getattr(after, column.name) for column in after.__table__.columns
    } == before


def test_standalone_access_redirect_and_revocation_without_concept(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "access-config", "Access")
    publish_dataset(s, a.id, d)
    login(client)
    user = s.users.create_user(a.id, "export-config@example.com", "Exporter", "valid password")
    csrf = session_csrf(client, s)
    response = request(
        client,
        "post",
        f"/datasets/{d.key}/export-access?inline=1&standalone=1",
        data={"csrf_token": csrf, "subject": f"user:{user.id}"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/configure#access-management")
    assert "Remove export access for Exporter" in body(request(client, "get", response.location))
    entry = s.datasets.export_access_context(a.id, d.key)[3][0][0]
    response = request(
        client,
        "post",
        f"/datasets/{d.key}/export-access/{entry.id}/revoke?inline=1&standalone=1",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 302
    assert response.location.endswith("/configure#access-management")
    assert not s.datasets.export_access_context(a.id, d.key)[3]


@pytest.mark.parametrize("published", [False, True])
def test_configuration_fields_are_read_only(dataset_browser, published):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "read-fields", "Read fields")
    s.dataset_fields.create_field(
        a.id,
        d.key,
        "code",
        "Visible code",
        "string",
        True,
        True,
        True,
        definition="Field explanation",
        unit="EUR",
    )
    if published:
        publish_dataset(s, a.id, d)
    login(client)
    html = body(request(client, "get", f"/datasets/{d.key}/configure"))
    fields = html.split("<section id='fields'", 1)[1].split("</section>", 1)[0]
    assert "Visible code" in fields and "Field explanation" in fields and "EUR" in fields
    assert "Identifier" in fields and "Required" in fields and "Unique" in fields
    assert ("Current · Version 1" if published else "Concept") + " · Read only" in fields
    assert "<form" not in fields and "<button" not in fields
    assert "field-edit-action" not in fields and "Add field" not in fields
    if published:
        current = body(request(client, "get", f"/datasets/{d.key}"))
        current_fields = current.split("<section id='fields'", 1)[1].split("</section>", 1)[0]
        assert "Current · Version 1 · Read only" in current_fields
        assert "Start a concept to change fields" in current_fields
        assert "field-drag-handle" not in current_fields
        s.dataset_versions.create_concept(a.id, d.id)
    concept = body(request(client, "get", f"/datasets/{d.key}/concept"))
    assert "field-drag-handle" in concept and "Start a concept" not in concept
    assert "Add field" in concept and "field-edit-action" in concept


def test_read_only_fields_do_not_disclose_concept_to_reader(dataset_browser):  # noqa: F811
    _, s, a, client = dataset_browser
    d = create_dataset(s, a, "field-authority", "Fields")
    s.dataset_fields.create_field(
        a.id, d.key, "code", "Published field", "string", True, True, True
    )
    publish_dataset(s, a.id, d)
    s.dataset_versions.create_concept(a.id, d.id)
    s.dataset_fields.create_field(a.id, d.key, "private", "Private concept field", "string")
    user = s.users.create_user(a.id, "fields-reader@example.com", "Reader", "valid password")
    login(client, user.email, "valid password")
    html = body(request(client, "get", f"/datasets/{d.key}/configure"))
    assert "Published field" in html and "Current · Version 1 · Read only" in html
    assert "Private concept field" not in html


def test_metadata_preserves_current_scheduled_and_expired_snapshots(context):  # noqa: F811
    from refrncr.version_contracts import version_states

    c = context
    setup(c)
    first = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Second", "string", False, False)
    second = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Third", "string", False, False)
    third = publish(c, day=NOW.date() + timedelta(days=2))
    states = version_states((first, second, third), NOW.date() + timedelta(days=1))
    assert {state.status for state in states} == {
        "Expired",
        "Current",
        "Scheduled",
    }
    before = snapshot(c)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Live name", "Live description", icon="database")
    c.service.create_concept(c.a.id, c.d.id)
    review = c.service.review_discard(c.a.id, c.d.id)
    assert not review.metadata_changes
    c.service.discard_concept(c.a.id, c.d.id, review.identity)
    after = snapshot(c)
    for table in before:
        if table.startswith("dataset_version"):
            assert after[table] == before[table], table
    for version in (first, second, third):
        assert content(c, version).name == c.d.name
    current = c.s.datasets.get_dataset_configuration(c.a.id, c.d.key).dataset
    assert (current.name, current.description, current.icon) == (
        "Live name",
        "Live description",
        "database",
    )


def test_simultaneous_dataset_metadata_saves_are_atomic(context):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from test_audit_coverage import audits

    c = context
    setup(c)
    first = publish(c)
    baseline = content(c, first)
    revision = c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).revision
    gate = Barrier(2)
    values = (("First", "First description", "house"), ("Second", "Second description", "database"))

    def save(values):
        gate.wait(timeout=10)
        name, description, icon = values
        c.s.datasets.update_dataset(c.a.id, c.d.key, name, description, icon=icon)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(save, values))
    saved = c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key)
    assert (saved.name, saved.description, saved.icon) in values
    assert saved.revision == revision
    assert content(c, first) == baseline
    assert len([a for a in audits(c) if a.action == "metadata_updated"]) == 2
