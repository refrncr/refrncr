"""Public generated documentation and protected data contract regressions."""

import json
from pathlib import Path

from fastapi.openapi.models import OpenAPI
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from sqlalchemy import func, select
from support import get

from refrncr.api.contracts import (
    Collection,
    Dataset,
    DatasetDetail,
    Detail,
    HierarchyRecordCollection,
    HierarchyResponse,
    RecordCollection,
    RecordDetail,
    VersionListItem,
)
from refrncr.api.http import SAFE_HEADERS
from refrncr.api.routes import create_api
from refrncr.models import AuditEvent

DATA_PATHS = {
    "/api/v1/datasets": Collection[Dataset],
    "/api/v1/datasets/{dataset_key}": Detail[DatasetDetail],
    "/api/v1/datasets/{dataset_key}/versions": Collection[VersionListItem],
    "/api/v1/datasets/{dataset_key}/records": RecordCollection | HierarchyRecordCollection,
    "/api/v1/datasets/{dataset_key}/records/{identifier}": RecordDetail,
    "/api/v1/datasets/{dataset_key}/lookup": RecordDetail,
    "/api/v1/datasets/{dataset_key}/hierarchy": HierarchyResponse,
}


def test_public_readme_has_no_internal_issue_references():
    readme = Path(__file__).resolve().parents[1] / "README.md"

    assert "REF-" not in readme.read_text(encoding="utf-8")


def test_public_documentation_has_no_data_or_audit(api):
    c = api
    with c.sessions() as session:
        before = session.scalar(select(func.count()).select_from(AuditEvent))
    responses = [c.client.get(path) for path in ("/api/openapi.json", "/api/docs")]
    for response in responses:
        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        for key, value in SAFE_HEADERS.items():
            if key != "Content-Security-Policy":
                assert response.headers[key] == value
        for private in (c.key.secret, c.u.id, c.u.email, c.d.id, c.d.name, c.record.id, "Café"):
            assert private not in response.text
    with c.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == before
    assert c.client.get("/api/docs", headers={"Authorization": "Bearer invalid"}).status_code == 200
    assert get(c, headers={}).status_code == 401
    assert get(c).status_code == 200


def test_schema_matches_registered_routes_and_contracts(api):
    schema = api.client.get("/api/openapi.json").json()
    OpenAPI.model_validate(schema)
    assert schema["openapi"].startswith("3.")
    assert set(schema["paths"]) == set(DATA_PATHS) | {"/api/openapi.json", "/api/docs"}
    assert "servers" not in schema
    source = create_api(None, None)
    routes = {"/api/v1" + r.path: r for r in source.routes if isinstance(r, APIRoute)}
    assert set(routes) == set(DATA_PATHS)
    models = schema["components"]["schemas"]
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["type"] == "http"
    for path, model in DATA_PATHS.items():
        assert routes[path].response_model == model
        assert routes[path].methods == {"GET"}
        assert set(schema["paths"][path]) == {"get"}
        op = schema["paths"][path]["get"]
        assert op["security"] == [{"HTTPBearer": []}]
        assert set(op["responses"]) == {"200", "304", "400", "401", "404", "405", "503"}
        response_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        expected = TypeAdapter(model).json_schema(
            mode="serialization", ref_template="#/components/schemas/{model}"
        )
        if "anyOf" in response_schema:
            assert response_schema["anyOf"] == expected["anyOf"]
        else:
            ref = response_schema["$ref"]
            expected.pop("$defs", None)
            assert models[ref.rsplit("/", 1)[1]] == expected
        assert "content" not in op["responses"]["304"]
        for status in ("200", "304"):
            assert set(op["responses"][status]["headers"]) == {
                "ETag",
                "Last-Modified",
                "Cache-Control",
                "Vary",
            }
        for status in ("400", "401", "404", "405", "503"):
            assert op["responses"][status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/Error"
            }
        params = {p["name"]: p for p in op["parameters"]}
        assert params["If-None-Match"]["in"] == params["If-Modified-Since"]["in"] == "header"
        collection = path.endswith(("datasets", "records", "versions"))
        expected_query = set() if path.endswith("versions") else {"effective_date", "version"}
        if collection:
            expected_query |= {"offset", "limit"}
        if path.endswith("/lookup"):
            expected_query.add("source")
            assert params["source"]["required"] is True
            assert params["source"]["schema"]["examples"] == ["1001"]
            assert "1001" in op["description"] and "AMS-SALES" in op["description"]
            assert "not a general filter" in op["description"]
            example = op["responses"]["200"]["content"]["application/json"]["example"]
            RecordDetail.model_validate(example)
            assert example["data"]["values"] == {"code": 1001, "target": "AMS-SALES"}
        assert {n for n, p in params.items() if p["in"] == "query"} == expected_query
        if not path.endswith("versions"):
            effective_date = params["effective_date"]
            assert "YYYY-MM-DD" in effective_date["description"]
            assert "2026-09-30" in effective_date["description"]
            assert effective_date["schema"]["anyOf"][0] == {
                "type": "string",
                "format": "date",
            }
            assert effective_date["example"] == "2026-09-30"
        if collection:
            for name, default, lower, upper in [
                ("offset", 0, 0, 2147483647),
                ("limit", 50, 1, 100),
            ]:
                assert {
                    k: params[name]["schema"][k] for k in ("type", "default", "minimum", "maximum")
                } == dict(type="integer", default=default, minimum=lower, maximum=upper)
        for p in params.values():
            assert p.get("description")
    for path in ("/api/openapi.json", "/api/docs"):
        assert not schema["paths"][path]["get"].get("security")
    assert "text/html" in schema["paths"]["/api/docs"]["get"]["responses"]["200"]["content"]
    assert (
        "application/json"
        in schema["paths"]["/api/openapi.json"]["get"]["responses"]["200"]["content"]
    )
    assert set(models["Record"]["properties"]) == {"identifier", "values"}
    assert "number" in json.dumps(models["Record"]["properties"]["values"])
    assert set(models["VersionMetadata"]["properties"]) == {
        "number",
        "published_at",
        "effective_from",
        "effective_to",
        "source",
    }
    assert set(models["VersionListItem"]["properties"]) == {
        *models["VersionMetadata"]["properties"],
        "status",
    }
    assert models["VersionListItem"]["properties"]["status"]["enum"] == [
        "Current",
        "Scheduled",
        "Expired",
    ]
    for response_model in ("Dataset", "RecordCollection", "RecordDetail"):
        assert "version" in models[response_model]["properties"]
        assert "current_version" not in models[response_model]["properties"]
    assert "source" not in models["VersionMetadata"]["required"]
    assert "mapping_role" not in models["FieldMetadata"]["required"]
    assert "HTTPValidationError" not in models

    # Every reference in the generated document resolves locally.
    def references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = schema
                for part in value["$ref"].removeprefix("#/").split("/"):
                    target = target[part]
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    references(schema)


def test_documentation_csp_assets_fallback_and_failure(api, monkeypatch):
    response = api.client.get("/api/docs")
    html = response.text
    assert '<html lang="en">' in html
    assert 'src="/static/images/refrncr-wordmark-primary.svg"' in html
    assert 'alt="refrncr"' in html
    assert "Published data API" in html
    assert "<noscript>" in html
    assert 'href="/api/openapi.json"' in html
    assert "docs/development/operations.md" in html
    assert "/healthz" not in html and "/readyz" not in html
    assert "<title>REST API documentation" in html
    assert "https://" not in html and "<script>" not in html
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    for path in (
        "js/swagger-ui-bundle.js",
        "js/api-docs.js",
        "css/swagger-ui.css",
        "css/layouts/api-docs.css",
        "images/refrncr-wordmark-primary.svg",
    ):
        assert api.client.get("/static/" + path).status_code == 200

    def fail():
        raise RuntimeError("private SQL secret")

    monkeypatch.setattr(api.app, "openapi", fail)
    failed = api.client.get("/api/openapi.json")
    assert failed.status_code == 503
    assert failed.json() == {"error": "service_unavailable"}
    assert failed.headers["content-security-policy"] == SAFE_HEADERS["Content-Security-Policy"]


def test_documentation_ui_privacy_configuration_executes_in_node():
    import subprocess

    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["node", "tests/javascript/api_docs.cjs", "src/refrncr/static/js/api-docs.js"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
