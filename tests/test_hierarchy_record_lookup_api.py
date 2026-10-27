"""Published record identifiers round-trip through the version-scoped detail API."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from support import NOW
from test_api_export_access import policy, special_dataset
from test_mapping_source_versions import publish_table

from refrncr.published_contracts import encode_identifier


def get(c, key, suffix="/records", *, headers=None, **selection):
    return c.client.get(
        f"/api/v1/datasets/{key}{suffix}",
        params=selection,
        headers=c.headers if headers is None else headers,
    )


def make_hierarchy(c, key="tree"):
    c.s.dataset_versions.clock = c.service.clock
    dataset = c.s.datasets.create_dataset(c.a.id, key, "Tree", "", c.owner.id, kind="hierarchy")

    def node(name, parent=None):
        return c.s.hierarchy.create_node(
            c.a.id,
            key,
            name,
            parent,
            expected_revision=c.s.datasets.get_dataset_for_edit(c.a.id, key).revision,
        )

    root = node("Root")
    leaves = [node("First", root.id), node("Second", root.id)]
    c.s.dataset_fields.create_field(
        c.a.id, key, "code", "Code", "string", True, False, hierarchy_scope="leaves"
    )
    for leaf, code in zip(leaves, ("first", "second"), strict=True):
        update_leaf(c, dataset, leaf, code)
    publish_table(c, dataset, NOW.date())
    return SimpleNamespace(c=c, dataset=dataset, root=root, leaves=leaves)


def update_leaf(c, dataset, leaf, code):
    c.s.hierarchy.update_values(
        c.a.id,
        dataset.key,
        leaf.id,
        {"code": code},
        expected_revision=c.s.datasets.get_dataset_for_edit(c.a.id, dataset.key).revision,
    )


@pytest.fixture
def hierarchy_lookup(database_api):
    return make_hierarchy(database_api)


@pytest.mark.parametrize("kind", ["reference", "mapping", "hierarchy", "hierarchy_without_key"])
def test_list_identifiers_round_trip_for_every_dataset_kind(database_api, kind):
    c = database_api
    if kind == "reference":
        dataset = c.d
    elif kind == "hierarchy_without_key":
        dataset = make_hierarchy(c).dataset
    else:
        dataset = special_dataset(c, kind)
    listing = get(c, dataset.key)
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]
    for row in listing.json()["data"]:
        detail = get(c, dataset.key, "/records/" + row["identifier"])
        assert detail.status_code == 200, detail.text
        assert detail.json() == {
            "data": {"identifier": row["identifier"], "values": row["values"]},
            "version": listing.json()["version"],
        }


def test_hierarchy_response_identifiers_round_trip_and_cache(hierarchy_lookup):
    h, c = hierarchy_lookup, hierarchy_lookup.c
    response = get(c, h.dataset.key, "/hierarchy")
    assert response.status_code == 200
    body = response.json()
    nodes = body["data"]["nodes"]
    records = body["data"]["records"]
    assert {node["record_identifier"] for node in nodes if node["record_identifier"]} == {
        row["identifier"] for row in records
    }
    for row in records:
        path = "/records/" + row["identifier"]
        detail = get(c, h.dataset.key, path)
        assert detail.status_code == 200, detail.text
        assert detail.json() == {"data": row, "version": body["version"]}
        cached = get(
            c, h.dataset.key, path, headers={**c.headers, "If-None-Match": detail.headers["etag"]}
        )
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached.headers["etag"] == detail.headers["etag"]
        assert "private" in cached.headers["cache-control"]


def test_synthetic_identifier_uses_selected_snapshot_and_stops_at_business_key(hierarchy_lookup):
    h, c = hierarchy_lookup, hierarchy_lookup.c
    key = h.dataset.key
    first = next(row for row in get(c, key).json()["data"] if row["values"]["code"] == "first")
    path = "/records/" + first["identifier"]
    old = get(c, key, path)
    c.service.create_concept(c.a.id, h.dataset.id)
    update_leaf(c, h.dataset, h.leaves[0], "updated")
    future = NOW.date() + timedelta(days=10)
    publish_table(c, h.dataset, future)
    for number, day, code in ((1, NOW.date(), "first"), (2, future, "updated")):
        for selection in (
            {"version": number},
            {"effective_date": day.isoformat()},
            {"version": number, "effective_date": day.isoformat()},
        ):
            listing = get(c, key, **selection).json()
            row = next(row for row in listing["data"] if row["identifier"] == first["identifier"])
            detail = get(c, key, path, **selection)
            assert detail.status_code == 200, detail.text
            assert detail.json() == {
                "data": {"identifier": first["identifier"], "values": {"code": code}},
                "version": listing["version"],
            }
            assert row["values"] == detail.json()["data"]["values"]
            cached = get(
                c,
                key,
                path,
                **selection,
                headers={**c.headers, "If-None-Match": detail.headers["etag"]},
            )
            assert cached.status_code == 304
    assert get(c, key, path).json()["data"] == old.json()["data"]
    assert get(c, key, path, version=2, effective_date=NOW.date().isoformat()).status_code == 400
    assert get(c, key, path, version=99).status_code == 404
    assert (
        get(c, key, path, effective_date=(NOW.date() - timedelta(days=1)).isoformat()).status_code
        == 404
    )

    c.service.clock.set(NOW + timedelta(days=11))
    current = get(c, key, path, headers={**c.headers, "If-None-Match": old.headers["etag"]})
    assert current.status_code == 200
    assert current.json()["data"]["values"] == {"code": "updated"}
    assert current.headers["etag"] != old.headers["etag"]
    assert get(c, key, path, version=1).json()["data"] == old.json()["data"]
    c.service.create_concept(c.a.id, h.dataset.id)
    c.s.dataset_business_keys.set_business_key(c.a.id, key, ("code",))
    publish_table(c, h.dataset)
    for selection in ({}, {"version": 3}, {"effective_date": c.service.clock.today().isoformat()}):
        response = get(
            c,
            key,
            path,
            **selection,
            headers={**c.headers, "If-None-Match": current.headers["etag"]},
        )
        assert response.status_code == 404
    assert get(c, key, path, version=1).status_code == 200
    assert get(c, key, path, version=2).status_code == 200
    for row in get(c, key).json()["data"]:
        detail = get(c, key, "/records/" + row["identifier"])
        assert detail.status_code == 200
        assert detail.json()["data"] == {"identifier": row["identifier"], "values": row["values"]}


def test_missing_foreign_branch_and_malformed_identifiers_are_404(hierarchy_lookup):
    h, c = hierarchy_lookup, hierarchy_lookup.c
    foreign = make_hierarchy(c, "other-tree")
    foreign_id = get(c, foreign.dataset.key).json()["data"][0]["identifier"]
    unknown = encode_identifier(b"hierarchy:" + str(uuid4()).encode("ascii"))
    branch = encode_identifier(b"hierarchy:" + h.root.id.encode("ascii"))
    valid = get(c, h.dataset.key).json()["data"][0]["identifier"]
    malformed = [
        encode_identifier(raw)
        for raw in (
            b"hierarchy:",
            b"hierarchy:not-a-uuid",
            b"hierarchy:" + b"a" * 36,
            b"hierarchy:\xff",
            b"other:" + h.leaves[0].id.encode("ascii"),
        )
    ]
    for identifier in (
        foreign_id,
        unknown,
        branch,
        *malformed,
        valid + "=",
        "!invalid",
        h.leaves[0].id,
    ):
        response = get(
            c, h.dataset.key, "/records/" + identifier, headers={**c.headers, "If-None-Match": "*"}
        )
        assert response.status_code == 404, response.text
        assert response.json() == {"error": "not_found"}
    for dataset in (c.d, special_dataset(c, "mapping"), special_dataset(c, "hierarchy")):
        assert get(c, dataset.key, "/records/" + valid).status_code == 404


@pytest.mark.parametrize("mode", ["team_grants", "admin_grants"])
@pytest.mark.parametrize("grant_type", ["user", "team"])
def test_hierarchy_detail_authorization_and_revocation_before_304(
    hierarchy_lookup, mode, grant_type
):
    h, c = hierarchy_lookup, hierarchy_lookup.c
    key = h.dataset.key
    row = get(c, key).json()["data"][0]
    path = "/records/" + row["identifier"]
    policy(c, mode)
    if grant_type == "team":
        c.s.memberships.create_membership(c.a.id, c.u.id, c.other.id)
    for selection in ({}, {"version": 1}, {"effective_date": NOW.date().isoformat()}):
        assert get(c, key, path, **selection).status_code == 404
    grant = c.s.datasets.grant_export_access(
        c.a.id, key, grant_type, c.u.id if grant_type == "user" else c.other.id
    )
    cached = get(c, key, path)
    assert cached.status_code == 200
    headers = {**c.headers, "If-None-Match": cached.headers["etag"]}
    assert get(c, key, path, headers=headers).status_code == 304
    c.s.datasets.revoke_export_access(c.a.id, key, grant.id)
    for selection in ({}, {"version": 1}, {"effective_date": NOW.date().isoformat()}):
        for validator in (cached.headers["etag"], "*"):
            denied = get(
                c, key, path, **selection, headers={**c.headers, "If-None-Match": validator}
            )
            assert denied.status_code == 404
            assert denied.json() == {"error": "not_found"}


def test_cancelled_hierarchy_snapshot_is_not_served_from_cache(hierarchy_lookup):
    h, c = hierarchy_lookup, hierarchy_lookup.c
    c.service.create_concept(c.a.id, h.dataset.id)
    update_leaf(c, h.dataset, h.leaves[0], "scheduled")
    second = publish_table(c, h.dataset, NOW.date() + timedelta(days=10))
    row = get(c, h.dataset.key, version=2).json()["data"][0]
    path = "/records/" + row["identifier"]
    cached = get(c, h.dataset.key, path, version=2)
    assert cached.status_code == 200
    c.service.cancel(c.a.id, h.dataset.id, second.id)
    denied = get(
        c,
        h.dataset.key,
        path,
        version=2,
        headers={**c.headers, "If-None-Match": cached.headers["etag"]},
    )
    assert denied.status_code == 404
