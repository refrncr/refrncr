"""Composition, pagination, privacy and atomic-publication regressions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import event, text
from support import NOW, csrf_token, get, publish

from refrncr.published_contracts import decode_identifier
from refrncr.services import AuthorizedResourceSet
from refrncr.version_persistence import SqlAlchemyVersionOperations


def test_flask_browser_assets_session_csrf_and_errors_alongside_api(api):

    c = api
    client = c.client
    assert client.get("/users", follow_redirects=False).status_code == 302
    assert client.get("/static/css/base.css").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/unknown-browser-route").status_code == 404
    assert (
        client.post(
            "/login", data={"email": "admin@example.com", "password": "valid password"}
        ).status_code
        == 403
    )
    token = csrf_token(client.get("/login"))
    login = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "valid password", "csrf_token": token},
        follow_redirects=False,
    )
    assert login.status_code == 302
    assert client.get("/users").status_code == 200
    assert get(c, headers={}).status_code == 401  # browser session never grants API access
    assert get(c).status_code == 200
    assert client.post("/logout").status_code == 403
    assert client.get("/users").status_code == 200
    assert client.get("/settings/api-keys").status_code == 200
    assert client.get("/static/missing.css").status_code == 404


def test_empty_records_limits_metadata_and_cookie_rejection(api):
    c = api
    d = c.s.datasets.create_dataset(c.a.id, "empty", "Empty", "", c.owner.id)
    c.s.dataset_fields.create_field(
        c.a.id,
        d.key,
        "code",
        "Code",
        "string",
        True,
        True,
        True,
        definition="Canonical code",
        unit="EUR",
    )
    c.service.publish(
        c.a.id, d.id, NOW.date(), "", c.s.datasets.get_dataset_for_edit(c.a.id, d.key).revision
    )
    response = get(c, "/empty/records?limit=100&offset=2147483647")
    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["pagination"]["total"] == 0
    field = get(c, "/empty").json()["data"]["fields"][0]
    assert (field["definition"], field["unit"]) == ("Canonical code", "EUR")
    assert get(c, headers={"Cookie": "api_key=" + c.key.secret}).status_code == 401
    assert get(c, "?api_key=" + c.key.secret, headers={}).status_code == 401


def test_dataset_pages_are_bounded_and_permissions_are_bulk(api, monkeypatch):
    c = api
    for number in range(8):
        d = c.s.datasets.create_dataset(c.a.id, f"more_{number}", f"More {number}", "", c.owner.id)
        c.s.dataset_fields.create_field(c.a.id, d.key, "code", "Code", "string", True, True, True)
        c.service.publish(
            c.a.id, d.id, NOW.date(), "", c.s.datasets.get_dataset_for_edit(c.a.id, d.key).revision
        )
    queries = []

    def capture(conn, cursor, statement, parameters, execution_context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(c.engine, "before_cursor_execute", capture)
    counts = []
    try:
        for size in (1, 5, 100):
            queries.clear()
            result = get(c, f"?limit={size}")
            assert result.status_code == 200
            assert len(result.json()["data"]) == min(size, 9)
            counts.append(len(queries))
            assert any("LIMIT" in q and "OFFSET" in q and "dataset_versions" in q for q in queries)
        assert len(set(counts)) == 1
        print("REF107 dataset SELECT counts:", counts)
    finally:
        event.remove(c.engine, "before_cursor_execute", capture)
    first = get(c)
    monkeypatch.setattr(
        c.service.authorization,
        "viewable_resources",
        lambda *a, **kw: AuthorizedResourceSet(False, (c.d.id,)),
    )
    restricted = get(c, headers={**c.headers, "If-None-Match": first.headers["etag"]})
    assert restricted.status_code == 200
    assert restricted.json()["pagination"]["total"] == 1
    assert restricted.json()["data"][0]["key"] == c.d.key


def test_concurrent_publication_never_mixes_representations(database_api, monkeypatch):
    c = database_api
    if c.engine.dialect.name == "sqlite":
        with c.engine.connect() as connection:
            connection.execute(text("PRAGMA journal_mode=WAL"))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "New publication"})
    start, done = Event(), Event()
    original = SqlAlchemyVersionOperations.record_page

    def paused(self, version, fields, **kwargs):
        start.set()
        assert done.wait(20)
        return original(self, version, fields, **kwargs)

    monkeypatch.setattr(SqlAlchemyVersionOperations, "record_page", paused)

    def writer():
        assert start.wait(20)
        try:
            return publish(c)
        finally:
            done.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(writer)
        first = get(c, "/audit/records")
        future.result()
    assert first.status_code == 200, first.text
    assert first.json()["version"]["number"] == 1
    assert len(first.json()["data"]) == 1
    monkeypatch.setattr(SqlAlchemyVersionOperations, "record_page", original)
    second = get(c, "/audit/records", headers={**c.headers, "If-None-Match": first.headers["etag"]})
    assert second.status_code == 200
    assert second.json()["version"]["number"] == 2
    assert len(second.json()["data"]) == 2


def test_authentication_storage_failure_fails_closed(api, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError("must never escape")

    monkeypatch.setattr(api.keys, "authenticate", fail)
    response = get(api)
    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}
    assert "etag" not in response.headers


def test_etag_is_specific_to_representation_and_principal(api):
    c = api
    paths = ["", "/audit", "/audit/records", "/audit/records?limit=1", "/audit/records?offset=1"]
    tags = [get(c, path).headers["etag"] for path in paths]
    assert len(set(tags)) == len(paths)
    key = c.keys.create_key(c.a.id, "admin reader")
    response = get(c, headers={"Authorization": "Bearer " + key.secret, "If-None-Match": tags[0]})
    assert response.status_code == 200
    c.now[0] += timedelta(days=90)
    assert get(c, headers={**c.headers, "If-None-Match": tags[0]}).status_code == 401


@pytest.mark.parametrize(
    "identifier",
    ["", "a", "=", "////", "_" * 2668, "AAAAAWE", "AAAAAHM", "AAAAAnN4=", "not-an-internal-uuid"],
)
def test_invalid_canonical_identifiers(identifier):
    with pytest.raises(ValueError, match="Invalid identifier"):
        decode_identifier(identifier)
