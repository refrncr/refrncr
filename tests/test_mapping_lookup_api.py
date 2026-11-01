"""Typed mapping lookup through the published API on SQLite and PostgreSQL."""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from support import NOW
from test_api_export_access import grant_role, policy, special_dataset
from test_mapping_source_versions import publish_table

from refrncr.installation_policy import EXPORT_POLICIES


@pytest.fixture(params=["integer", "string"])
def mapping_api(database_api, request):
    c = database_api
    c.s.dataset_versions.clock = c.service.clock
    source_owner = c.s.teams.create_team(c.a.id, "Source owners")
    source = c.s.datasets.create_dataset(
        c.a.id, "cost-centres", "Cost centres", "", source_owner.id
    )
    c.s.dataset_fields.create_field(
        c.a.id, source.key, "code", "Code", request.param, True, True, True
    )
    c.s.dataset_fields.create_field(c.a.id, source.key, "label", "Label", "string")
    codes = ("1001", "1002", "1003") if request.param == "integer" else ("001001", "AMS/2", "Café")
    records = [
        c.s.dataset_records.create_record(c.a.id, source.key, {"code": code, "label": code})
        for code in codes
    ]
    source_version = publish_table(c, source)
    mapping = c.s.datasets.create_dataset(
        c.a.id,
        "cost-centre-map",
        "Cost centre mapping",
        "",
        c.owner.id,
        kind="mapping",
        source_dataset_id=source.id,
        source_field_key="code",
    )
    c.s.dataset_fields.create_field(
        c.a.id,
        mapping.key,
        "label",
        "Label",
        "string",
        mapping_role="source_context",
        source_field_key="label",
    )
    c.s.dataset_fields.create_field(c.a.id, mapping.key, "target", "Target", "string")
    row = c.s.dataset_records.list_records(c.a.id, mapping.key).records[0]
    c.s.dataset_records.update_record(
        c.a.id, mapping.key, row.id, row.revision, {"target": "AMS-SALES"}
    )
    version = publish_table(c, mapping)
    return SimpleNamespace(
        c=c,
        source=source,
        mapping=mapping,
        datatype=request.param,
        codes=codes,
        source_records=records,
        source_version=source_version,
        version=version,
    )


def lookup(m, source=None, *, headers=None, **selection):
    return m.c.client.get(
        f"/api/v1/datasets/{m.mapping.key}/lookup",
        params={"source": m.codes[0] if source is None else source, **selection},
        headers=m.c.headers if headers is None else headers,
    )


def test_typed_lookup_matches_record_detail_and_preserves_unmapped_rows(mapping_api):
    m, c = mapping_api, mapping_api.c
    records = c.client.get(f"/api/v1/datasets/{m.mapping.key}/records", headers=c.headers).json()[
        "data"
    ]
    for source in m.codes:
        response = lookup(m, source)
        assert response.status_code == 200, response.text
        body = response.json()
        expected = int(source) if m.datatype == "integer" else source
        assert body["data"]["values"]["code"] == expected
        record = next(r for r in records if r["values"]["code"] == expected)
        assert body["data"] == record
        opaque = quote(record["identifier"], safe="")
        direct = c.client.get(
            f"/api/v1/datasets/{m.mapping.key}/records/{opaque}", headers=c.headers
        )
        assert body == direct.json()
        assert body["version"]["source"] == {"dataset_key": m.source.key, "version": 1}
    assert {r["values"]["target"] for r in records} == {None, "AMS-SALES"}
    assert lookup(m).json()["data"]["values"]["target"] == "AMS-SALES"
    assert lookup(m, m.codes[1]).json()["data"]["values"]["target"] is None
    if m.datatype == "string":
        assert lookup(m, "1001").status_code == 404  # Leading zeroes remain significant.
        assert lookup(m, "Cafe\u0301").json() == lookup(m, "Café").json()
    else:
        assert lookup(m, "01001").json() == lookup(m, "1001").json()


def test_mapping_version_list_includes_source(mapping_api):
    m = mapping_api
    response = m.c.client.get(f"/api/v1/datasets/{m.mapping.key}/versions", headers=m.c.headers)

    assert response.status_code == 200
    assert response.json()["data"][0]["source"] == {
        "dataset_key": m.source.key,
        "version": m.source_version.number,
    }


def test_lookup_invalid_unknown_and_missing_source(mapping_api):
    m = mapping_api
    assert lookup(m, "9999").status_code == 404
    invalid = ("abc", "1.5", "+1", "1e3") if m.datatype == "integer" else ("bad\x00value",)
    for source in (*invalid, "", " "):
        response = lookup(m, source)
        assert response.status_code == 400, response.text
        assert response.json() == {"error": "invalid_request"}
    path = f"/api/v1/datasets/{m.mapping.key}/lookup"
    for query in ("", "?source=1001&source=1002", "?source=1001&limit=1", "?source=" + "a" * 257):
        assert m.c.client.get(path + query, headers=m.c.headers).status_code == 400
    assert lookup(m, headers={}).status_code == 401


def test_lookup_pinned_source_and_historical_scheduled_selection(mapping_api):
    m, c = mapping_api, mapping_api.c
    c.s.dataset_versions.create_concept(c.a.id, m.source.id)
    removed = m.source_records[-1]
    c.s.dataset_records.delete_record(c.a.id, m.source.key, removed.id, removed.revision)
    c.s.dataset_records.create_record(c.a.id, m.source.key, {"code": "2000", "label": "New"})
    future = NOW.date() + timedelta(days=10)
    publish_table(c, m.source, future)
    # Publishing a newer source alone must not change the mapping's pinned metadata.
    assert lookup(m).json()["version"]["source"]["version"] == 1
    revision = c.s.datasets.get_dataset_for_edit(c.a.id, m.mapping.key).revision
    c.s.dataset_versions.update_mapping_source(c.a.id, m.mapping.id, revision)
    second = publish_table(c, m.mapping, future)
    for selection in (
        {"version": 1},
        {"effective_date": NOW.date().isoformat()},
        {"version": 1, "effective_date": NOW.date().isoformat()},
        {},
    ):
        response = lookup(m, m.codes[-1], **selection)
        assert response.status_code == 200, response.text
        assert response.json()["version"]["number"] == 1
        assert response.json()["version"]["source"]["version"] == 1
    for selection in (
        {"version": 2},
        {"effective_date": future.isoformat()},
        {"version": 2, "effective_date": future.isoformat()},
    ):
        assert lookup(m, m.codes[-1], **selection).status_code == 404
        response = lookup(m, "2000", **selection)
        assert response.status_code == 200, response.text
        assert response.json()["version"]["number"] == 2
        assert response.json()["version"]["source"]["version"] == 2
        cached = lookup(
            m, "2000", **selection, headers={**c.headers, "If-None-Match": response.headers["etag"]}
        )
        assert cached.status_code == 304
    assert lookup(m, "2000").status_code == 404
    assert lookup(m, version=2, effective_date=NOW.date().isoformat()).status_code == 400
    assert lookup(m, version=3).status_code == 404
    assert lookup(m, effective_date=(NOW.date() - timedelta(days=1)).isoformat()).status_code == 404
    c.service.clock.set(NOW + timedelta(days=11))
    assert lookup(m, m.codes[-1]).status_code == 404
    assert lookup(m, m.codes[-1], version=1).status_code == 200
    # Cancelled publications remain inaccessible, even when the client has an ETag.
    c.service.clock.set(NOW)
    cached = lookup(m, "2000", version=2)
    c.s.dataset_versions.cancel(c.a.id, m.mapping.id, second.id)
    assert (
        lookup(
            m, "2000", version=2, headers={**c.headers, "If-None-Match": cached.headers["etag"]}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("mode", EXPORT_POLICIES)
@pytest.mark.parametrize("role", ["none", "admin", "owner", "user_grant", "team_grant"])
def test_lookup_uses_mapping_export_access_in_each_policy(mapping_api, mode, role):
    m, c = mapping_api, mapping_api.c
    policy(c, "admin_grants")
    grant_role(c, role, m.mapping)
    policy(c, mode)
    response = lookup(m)
    allowed = mode == "everyone" or role != "none"
    assert response.status_code == (200 if allowed else 404), response.text
    if not allowed:
        assert (
            lookup(m, "bad\x00value", headers={**c.headers, "If-None-Match": "*"}).status_code
            == 404
        )
        return
    assert response.json()["version"]["source"] == {"dataset_key": m.source.key, "version": 1}
    source = c.client.get(f"/api/v1/datasets/{m.source.key}", headers=c.headers)
    # The source has another owning team. A mapping grant does not grant source access.
    assert source.status_code == (200 if mode == "everyone" or role == "admin" else 404)


def test_lookup_conditional_requests_and_revoked_mapping_grant(mapping_api):
    m, c = mapping_api, mapping_api.c
    policy(c, "admin_grants")
    grant = c.s.datasets.grant_export_access(c.a.id, m.mapping.key, "user", c.u.id)
    first = lookup(m)
    assert first.status_code == 200
    direct = c.client.get(
        f"/api/v1/datasets/{m.mapping.key}/records/{first.json()['data']['identifier']}",
        headers=c.headers,
    )
    assert first.headers["last-modified"] == direct.headers["last-modified"]
    date_headers = {**c.headers, "If-Modified-Since": first.headers["last-modified"]}
    direct_date = c.client.get(
        f"/api/v1/datasets/{m.mapping.key}/records/{first.json()['data']['identifier']}",
        headers=date_headers,
    )
    assert lookup(m, headers=date_headers).status_code == direct_date.status_code
    assert lookup(m, headers={**date_headers, "If-None-Match": '"different"'}).status_code == 200
    for validator in (
        first.headers["etag"],
        "W/" + first.headers["etag"],
        '"other", ' + first.headers["etag"],
        "*",
    ):
        cached = lookup(m, headers={**c.headers, "If-None-Match": validator})
        assert cached.status_code == 304 and cached.content == b""
        assert cached.headers["etag"] == first.headers["etag"]
        assert cached.headers["last-modified"] == first.headers["last-modified"]
        assert "private" in cached.headers["cache-control"]
    assert (
        lookup(
            m, m.codes[1], headers={**c.headers, "If-None-Match": first.headers["etag"]}
        ).status_code
        == 200
    )
    assert lookup(m, "9999", headers={**c.headers, "If-None-Match": "*"}).status_code == 404
    invalid = "bad" if m.datatype == "integer" else "bad\x00value"
    assert lookup(m, invalid, headers={**c.headers, "If-None-Match": "*"}).status_code == 400
    c.s.datasets.revoke_export_access(c.a.id, m.mapping.key, grant.id)
    denied = lookup(m, headers={**c.headers, "If-None-Match": first.headers["etag"]})
    assert denied.status_code == 404 and denied.json() == {"error": "not_found"}


def test_mapping_metadata_on_dataset_list_detail_and_records(mapping_api):
    m, c = mapping_api, mapping_api.c
    listed = c.client.get("/api/v1/datasets", headers=c.headers).json()["data"]
    mapping = next(d for d in listed if d["key"] == m.mapping.key)
    expected = {"dataset_key": m.source.key, "version": 1}
    assert mapping["version"]["source"] == expected
    detail = c.client.get(f"/api/v1/datasets/{m.mapping.key}", headers=c.headers).json()["data"]
    assert detail["version"]["source"] == expected
    assert {f["key"]: f["mapping_role"] for f in detail["fields"]} == {
        "code": "source_identifier",
        "label": "source_context",
        "target": "mapping_target",
    }
    records = c.client.get(f"/api/v1/datasets/{m.mapping.key}/records", headers=c.headers).json()
    assert records["version"]["source"] == expected


@pytest.mark.parametrize("kind", ["reference", "hierarchy"])
def test_non_mapping_lookup_is_not_found_and_metadata_is_null(database_api, kind):
    c = database_api
    dataset = c.d if kind == "reference" else special_dataset(c, "hierarchy")
    base = f"/api/v1/datasets/{dataset.key}"
    for suffix in ("?source=a", "?source=bad%00value"):
        response = c.client.get(base + "/lookup" + suffix, headers=c.headers)
        assert response.status_code == 404 and response.json() == {"error": "not_found"}
    detail = c.client.get(base, headers=c.headers).json()["data"]
    assert detail["version"]["source"] is None
    assert all(f["mapping_role"] is None for f in detail["fields"])
    records = c.client.get(base + "/records", headers=c.headers).json()
    assert records["version"]["source"] is None
    listed = c.client.get("/api/v1/datasets", headers=c.headers).json()["data"]
    assert next(d for d in listed if d["key"] == dataset.key)["version"]["source"] is None
    if kind == "reference":
        identifier = records["data"][0]["identifier"]
        assert (
            c.client.get(base + "/records/" + identifier, headers=c.headers).json()["version"][
                "source"
            ]
            is None
        )
    if kind == "hierarchy":
        hierarchy = c.client.get(base + "/hierarchy", headers=c.headers).json()
        assert hierarchy["version"]["source"] is None
        assert all(f["mapping_role"] is None for f in hierarchy["data"]["fields"])
