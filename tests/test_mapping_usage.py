"""Source-side mapping dependencies, lifecycle selection and permission-safe presentation."""

from datetime import timedelta

import pytest
from sqlalchemy import event
from support import NOW, body, html_elements, html_text, login, request, snapshot
from test_mapping_source_updates import browser, fixture, next_source, update
from test_mapping_source_versions import create_mapping, prepare, publish_table

from refrncr.services import ConflictError, NotFoundError


def usage(c, actor=None, **kwargs):
    return c.s.datasets.mapping_usage(actor or c.a.id, c.d.key, **kwargs)


def item(c, actor=None):
    result = usage(c, actor)
    assert result["total"] == 1
    assert result["restricted"] == []
    return result["mappings"][0]


def test_empty_and_draft_only_usage(database_context):
    c = prepare(database_context)
    publish_table(c, c.d)
    assert usage(c) == {
        "key": c.d.key,
        "total": 0,
        "mappings": [],
        "restricted": [],
        "deletion_error": None,
    }
    mapping = create_mapping(c)
    assert item(c) == {
        "name": mapping.name,
        "key": mapping.key,
        "team": c.owner.name,
        "version_id": None,
        "status": "Draft",
        "source_number": 1,
        "update_available": False,
        "draft_source_number": None,
    }
    # A never-published mapping is not visible in Community to ordinary readers.
    assert usage(c, c.u.id) == {
        "key": c.d.key,
        "total": 1,
        "mappings": [],
        "restricted": [{"team": c.owner.name, "count": 1}],
        "deletion_error": "This dataset can't be deleted because 1 mapping is based on it.",
    }
    assert usage(c, details=False)["total"] == 1
    confirmation = c.s.datasets.get_dataset_for_deletion(c.a.id, mapping.id)
    c.s.datasets.delete_dataset(
        c.a.id, mapping.id, confirmation.name, confirmation.expected_updated_at
    )
    assert usage(c)["total"] == usage(c, details=False)["total"] == 0


def test_current_mapping_and_updated_draft_are_one_dependency(database_context):
    c = database_context
    mapping, _, published = fixture(c)
    assert item(c)["source_number"] == 1
    assert not item(c)["update_available"]
    next_source(c)
    update(c, mapping)
    before = snapshot(c)
    owner = item(c)
    reader = item(c, c.u.id)
    assert (owner["version_id"], owner["status"], owner["source_number"]) == (
        published.id,
        "Current",
        1,
    )
    assert owner["update_available"] and owner["draft_source_number"] == 2
    assert reader == {**owner, "draft_source_number": None}
    assert usage(c, details=False)["total"] == 1
    assert snapshot(c) == before
    c.s.dataset_versions.discard_concept(
        c.a.id, mapping.id, c.s.dataset_versions.review_discard(c.a.id, mapping.id).identity
    )
    assert item(c)["draft_source_number"] is None


def test_scheduled_source_signals_then_cancellation_removes_signal(database_context):
    c = database_context
    fixture(c)
    second = next_source(c)
    assert item(c)["update_available"]
    assert item(c, c.u.id)["update_available"]
    c.s.dataset_versions.cancel(c.a.id, c.d.id, second.id)
    assert not item(c)["update_available"]


def test_scheduled_mapping_remains_private_then_becomes_current(database_context):
    c = database_context
    mapping, _, _ = fixture(c, publish=False)
    next_source(c)
    update(c, mapping)
    scheduled = publish_table(c, mapping, NOW.date() + timedelta(days=1))
    assert (item(c)["status"], item(c)["source_number"]) == ("Scheduled", 2)
    reader = item(c, c.u.id)
    assert reader["name"] == mapping.name  # Existing dataset-wide Settings remain readable.
    assert reader["status"] is reader["version_id"] is reader["source_number"] is None
    assert not reader["update_available"]
    c.service.clock.advance(days=1)
    assert (item(c, c.u.id)["status"], item(c, c.u.id)["version_id"]) == ("Current", scheduled.id)
    assert not item(c, c.u.id)["update_available"]


def test_current_mapping_wins_over_scheduled_successor(database_context):
    c = database_context
    mapping, _, published = fixture(c)
    next_source(c)
    update(c, mapping)
    scheduled = publish_table(c, mapping, NOW.date() + timedelta(days=1))
    assert item(c)["version_id"] == published.id
    assert item(c)["source_number"] == 1
    c.service.clock.advance(days=1)
    assert item(c)["version_id"] == scheduled.id
    assert item(c)["source_number"] == 2
    assert not item(c)["update_available"]
    assert usage(c)["total"] == 1  # Expired V1 does not create a second row.


def deny_mappings(c, monkeypatch, ids):
    original = c.s.authorization.datasets.can_view_dataset

    def permitted(actor, dataset_id, *, uow=None):
        return dataset_id not in ids and original(actor, dataset_id, uow=uow)

    monkeypatch.setattr(c.s.authorization.datasets, "can_view_dataset", permitted)


def test_restricted_mappings_group_by_owning_team(database_context, monkeypatch):
    c = database_context
    mapping, _, _ = fixture(c)
    second = create_mapping(c, "second-private")
    third = c.s.datasets.create_dataset(
        c.a.id,
        "third-private",
        "Third private",
        "",
        c.other.id,
        kind="mapping",
        source_dataset_id=c.d.id,
        source_field_key="code",
    )
    deny_mappings(c, monkeypatch, {mapping.id, second.id, third.id})
    result = usage(c)
    assert result["total"] == 3
    assert result["mappings"] == []
    assert result["restricted"] == [
        {"team": c.owner.name, "count": 2},
        {"team": c.other.name, "count": 1},
    ]
    assert usage(c, details=False)["total"] == 3


def test_source_authorization_applies_to_list_and_count(context, monkeypatch):
    c = context
    fixture(c)
    deny_mappings(c, monkeypatch, {c.d.id})
    for details in (False, True):
        for key in (c.d.key, "missing", "../invalid"):
            with pytest.raises(NotFoundError):
                c.s.datasets.mapping_usage(c.u.id, key, details=details)


def test_usage_never_loads_mapping_records_and_counter_skips_versions(context):
    c = context
    fixture(c)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    event.listen(c.engine, "before_cursor_execute", record)
    try:
        assert usage(c, details=False)["total"] == 1
        assert not any("dataset_versions" in sql for sql in statements)
        assert item(c)["source_number"] == 1
        assert not any(
            "dataset_records" in sql or "dataset_version_records" in sql for sql in statements
        )
    finally:
        event.remove(c.engine, "before_cursor_execute", record)


def page(client, path, **kwargs):
    response = request(client, "get", path, **kwargs)
    assert response.status_code == 200
    return body(response)


def test_settings_counter_and_publication_share_usage(context):
    c = context
    mapping, _, _ = fixture(c)
    client = browser(c)
    next_source(c)
    c.service.clock.advance(days=1)
    update(c, mapping)
    settings = page(client, f"/datasets/{c.d.key}")
    assert settings.index("id='used-by-mappings'") < settings.index("id='danger-zone'")
    assert "Used by 1 mapping" in settings
    assert "uses V1 · update available" in html_text(settings)
    assert "Draft uses V2" in html_text(settings)
    records = page(client, f"/datasets/{c.d.key}/records")
    link = next(
        attrs["href"]
        for tag, attrs in html_elements(records)
        if tag == "a" and "used-by-mappings" in attrs.get("href", "")
    )
    assert link == f"/datasets/{c.d.key}?tab=dataset-details#used-by-mappings"
    assert "id='used-by-mappings'" in page(client, link)
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "d"})
    for path in (f"/datasets/{c.d.key}/versions/review", f"/datasets/{c.d.key}/versions/publish"):
        html = page(client, path, follow_redirects=True)
        assert (
            "1 mapping is based on this dataset. "
            "Their owners will see that a new version is available." in html_text(html)
        )
        assert "uses V1 · update available" in html_text(html)
        assert "Draft uses V2" in html_text(html)
    for tag, attrs in html_elements(settings):
        if tag == "a" and attrs.get("href", "").startswith(f"/datasets/{mapping.key}"):
            assert request(client, "get", attrs["href"]).status_code == 200


def test_no_usage_block_counter_or_publication_notice_without_mappings(context):
    c = prepare(context)
    client = browser(c)
    for path in (
        f"/datasets/{c.d.key}/concept",
        f"/datasets/{c.d.key}/concept/records",
        f"/datasets/{c.d.key}/versions/review",
        f"/datasets/{c.d.key}/versions/publish",
    ):
        html = page(client, path, follow_redirects=True)
        assert "Used by" not in html
        assert "based on this dataset" not in html


def test_source_reader_sees_block_but_not_mapping_draft_details(context):
    c = context
    mapping, _, _ = fixture(c)
    next_source(c)
    update(c, mapping)
    create_mapping(c, "private-draft-name")
    client = browser(c)
    login(client, email=c.u.email, password="valid password")
    html = page(client, f"/datasets/{c.d.key}")
    assert "Used by 2 mappings" in html_text(html)
    assert "uses V1 · update available" in html_text(html)
    assert "Draft uses V2" not in html
    assert "private-draft-name" not in html
    assert "1 mapping you don't have access to" in html_text(html)
    assert "id='danger-zone'" not in html


def test_restricted_details_absent_from_all_rendered_lists(context, monkeypatch):
    c = context
    mapping, _, _ = fixture(c)
    deny_mappings(c, monkeypatch, {mapping.id})
    client = browser(c)
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "c"})
    for path in (
        f"/datasets/{c.d.key}",
        f"/datasets/{c.d.key}/versions/review",
        f"/datasets/{c.d.key}/versions/publish",
    ):
        html = page(client, path, follow_redirects=True)
        assert "1 mapping you don't have access to" in html_text(html)
        assert c.owner.name in html_text(html)
        assert f"/datasets/{mapping.key}" not in html
        assert "uses V1" not in html
        assert "update available" not in html
        assert "up to date" not in html


def test_mapping_names_and_teams_are_escaped(context):
    c = context
    mapping, _, _ = fixture(c)
    c.s.datasets.update_dataset(c.a.id, mapping.key, "<script>mapping</script>", "")
    c.s.teams.rename_team(c.a.id, c.owner.id, "<script>team</script>")
    html = page(browser(c), f"/datasets/{c.d.key}")
    assert "<script>mapping</script>" not in html
    assert "<script>team</script>" not in html
    assert "&lt;script&gt;mapping&lt;/script&gt;" in html
    assert "&lt;script&gt;team&lt;/script&gt;" in html


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("count", [1, 2])
def test_source_deletion_blocked_until_last_mapping_removed(database_context, published, count):
    from test_dataset_deletion import browser as deletion_browser

    c = prepare(database_context)
    publish_table(c, c.d)
    # A confirmation opened before the first mapping cannot bypass the write-side guard.
    confirmation = c.s.datasets.get_dataset_for_deletion(c.a.id, c.d.id)
    mappings = [create_mapping(c, f"mapping-{i}") for i in range(count)]
    if published:
        for mapping in mappings:
            c.s.dataset_fields.create_field(c.a.id, mapping.key, "target", "Target", "string")
            publish_table(c, mapping)
    dependency = "1 mapping is" if count == 1 else "2 mappings are"
    message = f"This dataset can't be deleted because {dependency} based on it."
    client, csrf = deletion_browser(c)
    path = f"/datasets/id/{c.d.id}/delete"
    data = {
        "csrf_token": csrf,
        "confirmation_name": confirmation.name,
        "expected_updated_at": confirmation.expected_updated_at,
    }
    before = snapshot(c)
    for operation in (
        lambda: c.s.datasets.get_dataset_for_deletion(c.a.id, c.d.id),
        lambda: c.s.datasets.delete_dataset(
            c.a.id, c.d.id, confirmation.name, confirmation.expected_updated_at
        ),
    ):
        with pytest.raises(ConflictError) as error:
            operation()
        assert str(error.value) == message
        assert snapshot(c) == before
    for method in ("get", "post"):
        response = request(client, method, path, **({"data": data} if method == "post" else {}))
        assert response.status_code == 409
        assert message in html_text(body(response))
        assert "try again" not in body(response)
        assert snapshot(c) == before
    settings = page(client, f"/datasets/{c.d.key}")
    assert message in html_text(settings)
    buttons = [
        attrs
        for tag, attrs in html_elements(settings)
        if tag == "button" and "dataset-delete-button" in attrs.get("class", "")
    ]
    assert len(buttons) == 1 and "disabled" in buttons[0]
    assert buttons[0]["aria-describedby"] == "dataset-delete-blocked"
    assert not any(
        tag == "a" and attrs.get("href", "").startswith(path)
        for tag, attrs in html_elements(settings)
    )
    assert snapshot(c) == before
    for mapping in mappings:
        target = c.s.datasets.get_dataset_for_deletion(c.a.id, mapping.id)
        c.s.datasets.delete_dataset(c.a.id, mapping.id, target.name, target.expected_updated_at)
    settings = page(client, f"/datasets/{c.d.key}")
    assert "dataset-delete-blocked" not in settings
    assert any(
        tag == "a" and attrs.get("href", "").startswith(path)
        for tag, attrs in html_elements(settings)
    )
    assert request(client, "get", path).status_code == 200
    assert request(client, "post", path, data=data).status_code == 303
    with pytest.raises(NotFoundError):
        c.s.datasets.get_dataset_for_deletion(c.a.id, c.d.id)
