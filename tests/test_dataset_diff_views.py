"""REF-84 browser contracts on SQLite and PostgreSQL; real Chrome is separate."""

import re
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from test_audit_coverage import audit_db, audits, context, snapshot  # noqa: F401
from test_dataset_version_views import client, configured, payload, request
from test_dataset_versions import NOW, publish


def prepared(c):
    configured(c)
    publish(c)
    c.service.clock = lambda: NOW + timedelta(days=1)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "new", "label": "<b>draft</b>"}
    )
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "New label", "string", False, False)
    browser, csrf = client(c)
    base = f"/datasets/{c.d.key}/versions/review"
    response = request(browser, "get", base)
    assert response.status_code == 303
    url = response.location
    token = parse_qs(urlsplit(url).query)["review"][0]
    return browser, csrf, base, token


def path(base, token, **values):
    return base + "?" + urlencode({"review": token, **values}, doseq=True)


def test_review_navigation_details_and_no_audit_noise(context):  # noqa: F811
    c = context
    browser, _csrf, base, token = prepared(c)
    before = snapshot(c)
    for section in ("Summary", "Fields", "Records"):
        response = request(browser, "get", path(base, token, section=section))
        assert response.status_code == 200
        assert "Draft revision" in response.text and "Version 1" in response.text
        assert "You’re working on a draft" in response.text and "Publish version" in response.text
        assert "no-store" in response.headers["Cache-Control"]
        if section == "Fields":
            assert "<span class='badge badge-draft'>Changed</span>" in response.text
            assert "<span class='badge badge-published'>Changed</span>" not in response.text
        if section == "Records":
            assert "<span class='badge badge-draft'>Changed</span>" in response.text
            assert "<span class='badge badge-published'>Changed</span>" not in response.text
    detail = request(
        browser,
        "get",
        path(base + f"/records/{c.record.id}", token, section="Records", q="new", status="Changed"),
    )
    assert detail.status_code == 200
    assert "&lt;b&gt;draft&lt;/b&gt;" in detail.text and "<b>draft</b>" not in detail.text
    assert "Before" in detail.text and "After" in detail.text
    assert "data-initial-focus" in detail.text
    assert "q=new" in detail.text and "status=Changed" in detail.text
    assert "publication-form" not in detail.text and "method='post'" not in detail.text
    field = c.s.dataset_fields.get_field(c.a.id, c.d.key, "label")
    response = request(browser, "get", path(base + f"/fields/{field.id}", token))
    assert response.status_code == 200 and "New label" in response.text
    assert snapshot(c) == before
    events = [item for item in audits(c) if item.action == "dataset.version_diff_viewed"]
    assert len(events) == 1


def test_review_status_badges_keep_added_and_removed_distinct(context):  # noqa: F811
    c = context
    browser, _csrf, base, _token = prepared(c)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "added", "label": "Added"})
    current = c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, c.record.id)
    c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, current.revision)
    entrypoint = request(browser, "get", base)
    token = parse_qs(urlsplit(entrypoint.location).query)["review"][0]
    response = request(browser, "get", path(base, token, section="Records"))
    assert response.status_code == 200
    statuses = re.findall(r"<span class='badge ([^']+)'>(Added|Removed)</span>", response.text)
    assert sorted(statuses) == sorted([("badge-draft", "Added"), ("badge-removed", "Removed")])


def test_entrypoints_and_publication_reuses_revision_csrf(context):  # noqa: F811
    c = context
    browser, csrf, base, token = prepared(c)
    versions = request(browser, "get", f"/datasets/{c.d.key}/versions")
    assert "Review changes" not in versions.text
    assert f"href='/datasets/{c.d.key}/versions/review'>Review &amp; publish</a>" in versions.text
    url = f"/datasets/{c.d.key}/versions/publish"
    assert "Review &amp; publish" in request(browser, "get", url).text
    panel = request(browser, "get", path(url, token))
    assert panel.status_code == 200 and "publication-form" in panel.text
    assert f"review={token}" in panel.text
    data = payload(c, csrf, day=NOW.date() + timedelta(days=1))
    assert (
        request(browser, "post", path(url, token), data=data | {"csrf_token": "bad"}).status_code
        == 403
    )
    result = request(browser, "post", path(url, token), data=data)
    assert result.status_code == 303 and "notice=published" in result.location
    assert len([item for item in audits(c) if item.action == "dataset.version_diff_viewed"]) == 1


def test_versions_review_entry_does_not_materialize_publication(context, monkeypatch):  # noqa: F811
    c = context
    browser, _csrf, _base, _token = prepared(c)
    before = snapshot(c)

    def forbidden(*args, **kwargs):
        raise AssertionError("Versions must not materialize a publication snapshot")

    monkeypatch.setattr(c.service, "review", forbidden)
    response = request(browser, "get", f"/datasets/{c.d.key}/versions")
    assert response.status_code == 200 and "Review changes" not in response.text
    assert f"href='/datasets/{c.d.key}/versions/review'>Review &amp; publish</a>" in response.text
    assert snapshot(c) == before


def test_first_publication_panel_offers_optional_review(context):  # noqa: F811
    c = context
    configured(c)
    browser, csrf = client(c)
    url = f"/datasets/{c.d.key}/versions/publish"
    before = snapshot(c)
    concept = request(browser, "get", f"/datasets/{c.d.key}/concept/records")
    assert concept.text.count("id='concept-publication-panel'") == 1
    assert "data-dialog-open='concept-publication-panel'" in concept.text
    panel = request(browser, "get", url)
    assert panel.status_code == 200 and "Review &amp; publish" in panel.text
    assert "publication-form" in panel.text
    assert snapshot(c) == before
    result = request(browser, "post", url, data=payload(c, csrf))
    assert result.status_code == 303 and "notice=published" in result.location
    assert not [item for item in audits(c) if item.action == "dataset.version_diff_viewed"]


def test_stale_routes_clear_content_and_reload_review(context):  # noqa: F811
    c = context
    browser, csrf, base, token = prepared(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Later", "string", False, False)
    before = snapshot(c)
    urls = [path(base, token, section=section) for section in ("Summary", "Fields", "Records")]
    urls += [
        path(base + f"/records/{c.record.id}", token),
        path(f"/datasets/{c.d.key}/versions/publish", token),
    ]
    for url in urls:
        response = request(browser, "get", url)
        assert response.status_code == 409 and "Reload review" in response.text
        assert "&lt;b&gt;draft" not in response.text and "publication-form" not in response.text
    url = path(f"/datasets/{c.d.key}/versions/publish", token)
    assert request(browser, "post", url, data=payload(c, "bad")).status_code == 403
    result = request(browser, "post", url, data=payload(c, csrf))
    assert result.status_code == 303
    assert request(browser, "get", result.location).status_code == 409
    assert snapshot(c) == before
    response = request(browser, "get", base)
    assert response.status_code == 303
    assert request(browser, "get", response.location).status_code == 200
    assert len([item for item in audits(c) if item.action == "dataset.version_diff_viewed"]) == 2


@pytest.mark.parametrize("kind", ["viewer", "editor", "export", "outsider", "inactive", "deleted"])
def test_direct_diff_routes_never_disclose_to_denied_actor(context, kind):  # noqa: F811
    c = context
    _admin, _csrf, base, token = prepared(c)
    if kind in {"viewer", "editor"}:
        c.s.grants.create_grant(c.a.id, "user", c.u.id, "dataset", c.d.id, kind)
    if kind == "export":
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    browser, _ = client(c, c.u)
    if kind == "inactive":
        c.s.users.disable_user(c.a.id, c.u.id)
    if kind == "deleted":
        c.s.users.delete_user(c.a.id, c.u.id)
    before = len(audits(c))
    urls = [base, path(base, token, section="Records", q="new", status="Changed")]
    for identity in (c.record.id, str(uuid4())):
        urls.extend(path(base + f"/{kind}/{identity}", token) for kind in ("records", "fields"))
    urls.append(path(f"/datasets/{c.d.key}/versions/publish", token))
    for url in urls:
        response = request(browser, "get", url)
        assert response.status_code == (302 if kind in {"inactive", "deleted"} else 404)
        assert "new" not in response.text and "draft" not in response.text
        assert "Scheduled" not in response.text and "Changed values" not in response.text
    new_events = audits(c)[before:]
    if kind in {"inactive", "deleted"}:
        assert [event.action for event in new_events] == ["security.session_rejected"]
    else:
        assert new_events == []


@pytest.mark.parametrize(
    "query",
    [
        "review=bad",
        "review=" + "x" * 1600,
        "section=Records",
        "review=bad&review=bad",
        "review=bad&q=' OR 1=1 --",
    ],
)
def test_malformed_review_links_are_safe(context, query):  # noqa: F811
    c = context
    browser, _csrf, base, _token = prepared(c)
    response = request(browser, "get", base + "?" + query)
    assert response.status_code in {400, 404, 409}
    assert "draft" not in response.text and "Traceback" not in response.text
    assert len([item for item in audits(c) if item.action == "dataset.version_diff_viewed"]) == 1


def test_forged_ids_session_and_search_bounds(context):  # noqa: F811
    c = context
    browser, _csrf, base, token = prepared(c)
    for suffix in (f"/records/{uuid4()}", f"/fields/{uuid4()}"):
        assert request(browser, "get", path(base + suffix, token)).status_code == 404
    assert request(browser, "get", path(base, token, q="x" * 201)).status_code == 400
    assert request(browser, "get", path(base, token, status="unexpected")).status_code == 400
    assert (
        request(browser, "get", path(base, token, section="Records", q="<script>")).status_code
        == 200
    )
    # Another active session cannot reuse a ticket, even for the same administrator.
    token2, _ = c.s.sessions.create(c.a)
    from refrncr.web import SESSION_COOKIE

    browser.set_cookie(SESSION_COOKIE, token2, domain="localhost", secure=True)
    assert request(browser, "get", path(base, token)).status_code == 409


def test_review_signature_shared_workers_and_key_rotation(context):  # noqa: F811
    import secrets

    from refrncr.web import SESSION_COOKIE, create_flask_app

    c = context
    configured(c)
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Reviewable draft", "string", False, False
    )
    session_token, _session = c.s.sessions.create(c.a)
    shared_key = secrets.token_bytes(32)

    def worker(key):
        app = create_flask_app(
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
            review_signing_key=key,
        )
        browser = app.test_client()
        browser.set_cookie(SESSION_COOKIE, session_token, domain="localhost", secure=True)
        return browser

    first, second = worker(shared_key), worker(shared_key)
    response = request(first, "get", f"/datasets/{c.d.key}/versions/review")
    assert response.status_code == 303
    before = snapshot(c)
    assert request(second, "get", response.location).status_code == 200
    assert request(worker(secrets.token_bytes(32)), "get", response.location).status_code == 409
    assert snapshot(c) == before


def test_review_websecurity_methods_parameters_and_escaping(context):  # noqa: F811
    c = context
    browser, _csrf, base, token = prepared(c)
    before = snapshot(c)
    detail = path(base + f"/records/{c.record.id}", token)
    for url in (path(base, token), detail):
        for method in ("post", "put", "patch", "delete"):
            assert request(browser, method, url).status_code == 405
        response = request(browser, "get", url)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
    for parameters in (
        {"q": "x" * 201},
        {"q": ["one", "two"]},
        {"status": ["Added"] * 4},
        {"section": ["Fields", "Records"]},
        {"unexpected": "value"},
        {"backwards": "2"},
        {"anchor": "x" * 4096},
    ):
        response = request(browser, "get", path(base, token, **parameters))
        assert response.status_code == 400
    for number in ("0", "-1", "2147483648", "1" * 11):
        response = request(
            browser, "get", path(base + f"/records/{c.record.id}", token, detail_page=number)
        )
        assert response.status_code == 400
    attack = '"><script>alert("content")</script>'
    response = request(browser, "get", path(base, token, section="Records", q=attack))
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text and attack not in response.text
    assert snapshot(c) == before


def test_review_failure_hides_internal_details(context, monkeypatch):  # noqa: F811
    from refrncr.dataset_diff import DatasetDiffService

    c = context
    browser, _csrf, base, token = prepared(c)
    before = snapshot(c)

    def fail(*args, **kwargs):
        raise RuntimeError("SELECT private FROM secret: constraint violation at /private/db.py")

    monkeypatch.setattr(DatasetDiffService, "record_detail", fail)
    response = request(browser, "get", path(base + f"/records/{c.record.id}", token))
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    for internal in ("SELECT", "constraint", "/private/", "Traceback", "RuntimeError"):
        assert internal not in response.text
    assert snapshot(c) == before
