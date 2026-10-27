"""Flask response policy and template guards; API policies remain independent."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from support import create_dataset
from test_interfaces import configured_services, login, setup_admin
from test_session_security import configured_web

from refrncr.api.http import SAFE_HEADERS
from refrncr.web import create_flask_app

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


def assert_html_policy(response):
    assert response.headers["Content-Security-Policy"] == CSP
    assert "Content-Security-Policy-Report-Only" not in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] in {"same-origin", "no-referrer"}
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"


@pytest.fixture
def web():
    _, users, authentication, sessions, teams, memberships = configured_services()
    app = create_flask_app(users, authentication, sessions, teams, memberships)

    @app.get("/unexpected-error")
    def unexpected_error():
        raise RuntimeError("private error details")

    return app.test_client()


def test_html_policy_on_setup_login_authenticated_pages_and_errors(web):
    setup = web.get("/setup", base_url="https://localhost")
    assert setup.status_code == 200
    assert_html_policy(setup)
    setup_admin(web)
    assert_html_policy(web.get("/login", base_url="https://localhost"))
    login(web)
    for method, path, status in (
        ("post", "/logout", 403),
        ("get", "/missing-page", 404),
        ("get", "/unexpected-error", 500),
        ("get", "/logout", 405),
    ):
        response = getattr(web, method)(path, base_url="https://localhost")
        assert response.status_code == status
        assert_html_policy(response)
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
        assert "private error details" not in response.text
    redirect = login(web)
    assert redirect.status_code == 302
    assert_html_policy(redirect)
    assert redirect.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert_html_policy(web.get("/users", base_url="https://localhost"))


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_transport_policy_on_html_static_and_304_responses(scheme):
    _, _, client = configured_web(allow_loopback_http=True)
    base = scheme + "://localhost"
    asset = client.get("/static/css/refrncr.css", base_url=base)
    assert asset.status_code == 200
    cached = client.get(
        "/static/css/refrncr.css", base_url=base, headers={"If-None-Match": asset.headers["ETag"]}
    )
    assert cached.status_code == 304
    for response in (client.get("/setup", base_url=base), asset, cached):
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        if scheme == "https":
            assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
        else:
            assert "Strict-Transport-Security" not in response.headers
    assert "Content-Security-Policy" not in asset.headers
    forwarded = client.get(
        "/setup", base_url="http://localhost", headers={"X-Forwarded-Proto": "https"}
    )
    assert "Strict-Transport-Security" not in forwarded.headers


@pytest.mark.parametrize("export_format", ["csv", "xlsx", "json"])
def test_download_headers_and_stricter_referrer_policy(dataset_browser, export_format):
    _, services, admin, client = dataset_browser
    login(client)

    dataset = create_dataset(services, admin, "headers", "Headers")
    services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", True, True, True
    )
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "NL"})
    response = client.get(
        f"/datasets/headers/concept/records/export?format={export_format}",
        base_url="https://localhost",
    )
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert "Content-Security-Policy" not in response.headers
    for path in (
        "/datasets/headers/concept/records",
        "/settings/audit-log",
    ):
        response = client.get(path, base_url="https://localhost")
        assert response.status_code == 200
        assert_html_policy(response)
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"


def test_api_and_documentation_keep_their_separate_policies(api):
    for path in ("/api/v1/datasets", "/api/openapi.json", "/api/docs"):
        response = api.client.get(path, headers=api.headers)
        assert response.status_code == 200
        for name, value in SAFE_HEADERS.items():
            if name not in {"Content-Security-Policy", "Cache-Control"}:
                assert response.headers[name] == value
        assert "Strict-Transport-Security" not in response.headers
        assert "Cross-Origin-Opener-Policy" not in response.headers
        assert response.headers["Content-Security-Policy"] != CSP
    assert_html_policy(api.client.get("/login"))


class InlineContentGuard(HTMLParser):
    """Static security guard; Jinja expressions are left intact while parsing tags."""

    def __init__(self):
        super().__init__()
        self.violations = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "style" or (tag == "script" and not attributes.get("src")):
            self.violations.append(tag)
        for name, value in attrs:
            if name == "style" or name.startswith("on"):
                self.violations.append(name)
            if name in {"href", "src", "action", "formaction"} and value:
                if value.lstrip().lower().startswith("javascript:"):
                    self.violations.append("javascript URL")

    handle_startendtag = handle_starttag


def test_production_templates_have_no_inline_executable_content():
    root = Path(__file__).parents[1] / "src/refrncr/templates"
    for path in root.rglob("*.html"):
        guard = InlineContentGuard()
        guard.feed(path.read_text())
        assert not guard.violations, (path.relative_to(root), guard.violations)


@pytest.mark.parametrize(
    "markup",
    [
        "<script>alert(1)</script>",
        "<SCRIPT>alert(1)</SCRIPT>",
        "<style>body{color:red}</style>",
        '<img src=x onerror="alert(1)">',
        '<button ONCLICK="run()">Run</button>',
        '<div style="display:none"></div>',
        '<a href="javascript:alert(1)">Link</a>',
    ],
)
def test_template_guard_rejects_inline_content(markup):
    guard = InlineContentGuard()
    guard.feed(markup)
    assert guard.violations
