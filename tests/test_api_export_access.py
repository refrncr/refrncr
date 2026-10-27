"""Published API dataset authority, independent of version selection and app reading."""

from datetime import timedelta
from urllib.parse import quote

import pytest
from sqlalchemy import event, select
from support import NOW, publish
from test_mapping_source_versions import create_mapping, publish_table

from refrncr.installation_policy import EXPORT_POLICIES
from refrncr.models import AuditEvent
from refrncr.services import AuthorizationError, NotFoundError
from refrncr.web import SESSION_COOKIE


def policy(c, value):
    settings = c.s.installation_settings
    settings.change_policy(c.a.id, settings.get_policy(c.a.id), value, confirmed=True)


def get(c, suffix="", *, headers=None):
    return c.client.get("/api/v1/datasets" + suffix, headers=headers or c.headers)


def grant_role(c, role, dataset):
    if role == "admin":
        key = c.keys.create_key(c.a.id, "Administrator")
        c.headers = {"Authorization": "Bearer " + key.secret}
    elif role == "owner":
        c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    elif role == "user_grant":
        c.s.datasets.grant_export_access(c.a.id, dataset.key, "user", c.u.id)
    elif role == "team_grant":
        c.s.memberships.create_membership(c.a.id, c.u.id, c.other.id)
        c.s.datasets.grant_export_access(c.a.id, dataset.key, "team", c.other.id)


@pytest.mark.parametrize("mode", EXPORT_POLICIES)
@pytest.mark.parametrize("role", ["admin", "owner", "user_grant", "team_grant", "none"])
def test_policy_roles_and_all_version_selectors(database_api, mode, role):
    c = database_api
    identifier = quote(get(c, "/audit/records").json()["data"][0]["identifier"], safe="")
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Scheduled"}
    )
    publish(c, day=NOW.date() + timedelta(days=21))
    policy(c, "admin_grants")
    grant_role(c, role, c.d)
    policy(c, mode)
    allowed = mode == "everyone" or role != "none"
    selectors = [
        ("", 1),
        ("?version=1", 1),
        ("?effective_date=2026-09-10", 1),
        ("?version=1&effective_date=2026-09-10", 1),
        ("?version=2", 2),
        ("?effective_date=2026-10-01", 2),
        ("?version=2&effective_date=2026-10-01", 2),
    ]
    for query, number in selectors:
        listed = get(c, query)
        assert listed.status_code == 200, listed.text
        assert listed.json()["pagination"]["total"] == int(allowed)
        assert [d["key"] for d in listed.json()["data"]] == ([c.d.key] if allowed else [])
        for path in ("/audit", "/audit/records", "/audit/records/" + identifier):
            response = get(c, path + query)
            assert response.status_code == (200 if allowed else 404), response.text
            if allowed:
                data = response.json()
                version = data["data"]["version"] if path == "/audit" else data["version"]
                assert version["number"] == number
            else:
                assert response.content == get(c, "/absent" + query).content
    # A previous publication remains readable by version after its successor is effective.
    c.service.clock.set(NOW + timedelta(days=22))
    assert get(c, "/audit?version=1").status_code == (200 if allowed else 404)
    # Browser reads retain Community view access even when the API denies access.
    token, _ = c.s.sessions.create(c.u)
    c.client.cookies.set(SESSION_COOKIE, token)
    browser = c.client.get("/datasets/audit")
    assert browser.status_code == 200
    assert "Business name" in browser.text


def special_dataset(c, kind):
    c.s.dataset_versions.clock = c.service.clock
    if kind == "mapping":
        dataset = create_mapping(c)
        c.s.dataset_fields.create_field(
            c.a.id,
            dataset.key,
            "label",
            "Label",
            "string",
            mapping_role="source_context",
            source_field_key="label",
        )
        c.s.dataset_fields.create_field(c.a.id, dataset.key, "target", "Target", "string")
    else:
        dataset = c.s.datasets.create_dataset(
            c.a.id, "hierarchy", "Hierarchy", "", c.owner.id, kind="hierarchy"
        )
        root = c.s.hierarchy.create_node(
            c.a.id, dataset.key, "Root", expected_revision=dataset.revision
        )
        leaf = c.s.hierarchy.create_node(
            c.a.id,
            dataset.key,
            "Leaf",
            root.id,
            expected_revision=c.s.datasets.get_dataset_for_edit(c.a.id, dataset.key).revision,
        )
        c.s.dataset_fields.create_field(
            c.a.id,
            dataset.key,
            "code",
            "Code",
            "string",
            True,
            True,
            True,
            hierarchy_scope="leaves",
        )
        c.s.hierarchy.update_values(
            c.a.id,
            dataset.key,
            leaf.id,
            {"code": "leaf"},
            expected_revision=c.s.datasets.get_dataset_for_edit(c.a.id, dataset.key).revision,
        )
    publish_table(c, dataset, day=NOW.date())
    return dataset


@pytest.mark.parametrize("mode", EXPORT_POLICIES)
@pytest.mark.parametrize("kind", ["mapping", "hierarchy"])
def test_mapping_and_hierarchy_use_their_own_export_right(database_api, mode, kind):
    c = database_api
    dataset = special_dataset(c, kind)
    base = "/" + dataset.key
    first = get(c, base + "/records")
    assert first.status_code == 200, first.text
    identifier = quote(first.json()["data"][0]["identifier"], safe="")
    paths = [base, base + "/records", base + "/records/" + identifier]
    if kind == "hierarchy":
        paths.append(base + "/hierarchy")
    policy(c, mode)
    for path in paths:
        assert get(c, path).status_code == (200 if mode == "everyone" else 404)
    if mode != "everyone":
        c.s.datasets.grant_export_access(c.a.id, dataset.key, "user", c.u.id)
    for path in paths:
        assert get(c, path).status_code == 200
    if kind == "mapping":
        values = get(c, base + "/records").json()["data"][0]["values"]
        assert values["code"] == "a" and values["label"] == "Café"
        assert get(c, "/audit").status_code == (200 if mode == "everyone" else 404)
        assert get(c, "/audit/records").status_code == (200 if mode == "everyone" else 404)
    assert {d["key"] for d in get(c).json()["data"]} == (
        {c.d.key, dataset.key} if mode == "everyone" else {dataset.key}
    )


@pytest.mark.parametrize("mode", ["team_grants", "admin_grants"])
@pytest.mark.parametrize(
    "change",
    ["user_grant", "team_grant", "owner_membership", "grant_membership", "owner", "policy"],
)
def test_revocation_invalidates_detail_and_list_caches(database_api, mode, change):
    c = database_api
    policy(c, mode)
    if change == "user_grant":
        grant = c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    elif change in {"team_grant", "grant_membership"}:
        membership = c.s.memberships.create_membership(c.a.id, c.u.id, c.other.id)
        grant = c.s.datasets.grant_export_access(c.a.id, c.d.key, "team", c.other.id)
    elif change in {"owner", "owner_membership"}:
        membership = c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    else:
        policy(c, "everyone")
    identifier = quote(get(c, "/audit/records").json()["data"][0]["identifier"], safe="")
    paths = ["", "/audit", "/audit/records", "/audit/records/" + identifier]
    cached = {path: get(c, path) for path in paths}
    assert all(response.status_code == 200 for response in cached.values())
    for path, response in cached.items():
        assert (
            get(
                c, path, headers={**c.headers, "If-None-Match": response.headers["etag"]}
            ).status_code
            == 304
        )
    if change in {"user_grant", "team_grant"}:
        c.s.datasets.revoke_export_access(c.a.id, c.d.key, grant.id)
    elif change in {"owner_membership", "grant_membership"}:
        team = c.owner if change == "owner_membership" else c.other
        c.s.memberships.delete_membership(c.a.id, team.id, membership.id)
    elif change == "owner":
        c.s.datasets.change_owner(c.a.id, c.d.key, c.owner.id, c.other.id)
    else:
        policy(c, mode)
    with c.sessions() as session:
        before = tuple(session.scalars(select(AuditEvent.id)))
    for path, old in cached.items():
        headers = {**c.headers, "If-None-Match": old.headers["etag"]}
        response = get(c, path, headers=headers)
        if path:
            assert response.status_code == 404
            assert response.json() == {"error": "not_found"}
            for validator in (
                {"If-None-Match": "*"},
                {"If-Modified-Since": old.headers["last-modified"]},
            ):
                assert get(c, path, headers=c.headers | validator).status_code == 404
        else:
            assert response.status_code == 200
            assert response.json()["data"] == []
            assert response.json()["pagination"]["total"] == 0
            assert response.headers["etag"] != old.headers["etag"]
    with c.sessions() as session:
        assert tuple(session.scalars(select(AuditEvent.id))) == before


@pytest.mark.parametrize("mode", EXPORT_POLICIES)
@pytest.mark.parametrize("role", ["admin", "user"])
def test_api_key_page_explains_restricted_policy(database_api, mode, role):
    c = database_api
    policy(c, mode)
    actor = c.a if role == "admin" else c.u
    token, _ = c.s.sessions.create(actor)
    c.client.cookies.set(SESSION_COOKIE, token)
    page = c.client.get("/settings/api-keys")
    assert page.status_code == 200, page.text
    explanation = "A key reads the datasets you can export."
    assert (explanation in page.text) == (mode != "everyone")
    if mode != "everyone":
        assert page.text.index("Create API key") < page.text.index(explanation)
    if role == "user":
        assert c.client.get("/settings/dataset-access").status_code == 403
        with pytest.raises(AuthorizationError):
            c.s.installation_settings.get_policy(actor.id)


@pytest.mark.parametrize("mode", EXPORT_POLICIES)
def test_inactive_users_cannot_read_policy_or_exportable_resources(database_api, mode):
    c = database_api
    policy(c, mode)
    c.s.users.disable_user(c.a.id, c.u.id)
    with pytest.raises(AuthorizationError):
        c.s.installation_settings.requires_export_access(c.u.id)
    with pytest.raises(NotFoundError):
        c.service.authorization.exportable_resources(c.u.id)
    assert get(c).status_code == get(c, "/audit").status_code == 401


@pytest.mark.parametrize("mode", ["team_grants", "admin_grants"])
@pytest.mark.parametrize(
    "query",
    ["", "&version=1", "&effective_date=2026-09-10", "&version=1&effective_date=2026-09-10"],
)
def test_filtered_pagination_deduplicates_grants_without_n_plus_one(database_api, mode, query):
    c = database_api
    c.s.dataset_versions.clock = c.service.clock
    policy(c, mode)
    c.s.memberships.create_membership(c.a.id, c.u.id, c.other.id)
    allowed = []
    for key in ("a-hidden", "b-granted", "c-hidden", "d-owned", "e-granted"):
        owned = key == "d-owned"
        dataset = c.s.datasets.create_dataset(
            c.a.id, key, key, "", c.other.id if owned else c.owner.id
        )
        c.s.dataset_fields.create_field(c.a.id, key, "code", "Code", "string", True, True, True)
        publish_table(c, dataset, day=NOW.date())
        if key.endswith("granted") or owned:
            allowed.append(key)
            c.s.datasets.grant_export_access(c.a.id, key, "user", c.u.id)
            c.s.datasets.grant_export_access(c.a.id, key, "team", c.other.id)
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(c.engine, "before_cursor_execute", capture)
    try:
        counts = []
        for limit in (1, 50):
            statements.clear()
            response = get(c, f"?limit={limit}" + query)
            assert response.status_code == 200, response.text
            assert response.json()["pagination"]["total"] == 3
            assert [d["key"] for d in response.json()["data"]] == allowed[:limit]
            counts.append(len(statements))
        assert counts[0] == counts[1]
        url = "/api/v1/datasets?limit=1" + query
        actual = []
        while url:
            response = c.client.get(url, headers=c.headers)
            assert response.status_code == 200
            assert response.json()["pagination"]["total"] == 3
            actual.extend(d["key"] for d in response.json()["data"])
            url = response.json()["links"]["next"]
        assert actual == allowed
        empty = get(c, "?offset=3&limit=1" + query).json()
        assert empty["data"] == [] and empty["pagination"]["total"] == 3
    finally:
        event.remove(c.engine, "before_cursor_execute", capture)
