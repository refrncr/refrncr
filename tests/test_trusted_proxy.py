from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from refrncr.app import create_application, create_community_services
from refrncr.authorization import ResourceTypeRegistry
from refrncr.db.base import Base
from refrncr.identity.login_security import SourceIdentityResolver
from refrncr.operational import OperationalMiddleware
from refrncr.persistence.database import create_database_engine, create_session_factory
from refrncr.web.proxy import TrustedProxyMiddleware
from refrncr.web.security import HTTPS_ANONYMOUS_CSRF_COOKIE, HTTPS_SESSION_COOKIE

PASSWORD = "correct horse battery staple"


class ScopeRecorder:
    def __init__(self) -> None:
        self.scope = None

    async def __call__(self, scope, receive, send) -> None:
        self.scope = scope


def invoke_middleware(*, peer="10.0.0.2", headers=(), scheme="http"):
    recorder = ScopeRecorder()
    resolver = SourceIdentityResolver.from_config(("10.0.0.0/8",))
    middleware = TrustedProxyMiddleware(recorder, resolver)
    scope = {
        "type": "http",
        "scheme": scheme,
        "client": (peer, 1234),
        "headers": headers,
    }

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    asyncio.run(middleware(scope, receive, send))
    return recorder.scope


def test_trusted_proxy_recognizes_one_trimmed_case_insensitive_https_header():
    scope = invoke_middleware(headers=((b"x-forwarded-proto", b" HTTPS "),))
    assert scope["scheme"] == "https"


def test_ipv4_mapped_ipv6_direct_peer_uses_ipv4_proxy_network():
    scope = invoke_middleware(
        peer="::ffff:10.0.0.2",
        headers=((b"x-forwarded-proto", b"https"),),
    )
    assert scope["scheme"] == "https"


def test_forwarded_scheme_fails_closed_for_untrusted_missing_duplicate_or_invalid_input():
    cases = (
        {"peer": "192.0.2.1", "headers": ((b"x-forwarded-proto", b"https"),)},
        {"headers": ()},
        {
            "headers": (
                (b"x-forwarded-proto", b"https"),
                (b"x-forwarded-proto", b"https"),
            )
        },
        {"headers": ((b"x-forwarded-proto", b"https, https"),)},
        {"headers": ((b"x-forwarded-proto", b"http"),)},
        {"headers": ((b"forwarded", b"for=198.51.100.1;proto=https"),)},
        {"headers": ((b"x-forwarded-host", b"community.example"),)},
    )
    for case in cases:
        scope = invoke_middleware(**case)
        assert scope["scheme"] == "http"


def configured_application():
    engine = create_database_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    services = create_community_services(create_session_factory(engine), ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    services.users.create_initial_admin("admin@example.com", "Administrator", PASSWORD)
    application = create_application(services, trusted_proxies=("10.0.0.0/8",))
    return services, TestClient(
        application,
        base_url="http://community.example",
        client=("10.0.0.2", 1234),
    )


def csrf(response) -> str:
    return response.text.split("name='csrf_token' value='", 1)[1].split("'", 1)[0]


def test_trusted_proxy_https_enables_secure_browser_login_and_hsts():
    _, client = configured_application()
    headers = {"X-Forwarded-Proto": "https"}

    middleware = [item.cls for item in client.app.user_middleware]
    assert middleware[:2] == [TrustedProxyMiddleware, OperationalMiddleware]

    login_form = client.get("/login", headers=headers)
    assert login_form.status_code == 200
    assert login_form.headers["strict-transport-security"] == "max-age=31536000"
    csrf_cookie = login_form.headers["set-cookie"]
    assert csrf_cookie.startswith(f"{HTTPS_ANONYMOUS_CSRF_COOKIE}=")
    assert "Secure" in csrf_cookie and "Domain=" not in csrf_cookie

    csrf_value = csrf(login_form)
    response = client.post(
        "/login",
        headers={
            **headers,
            "Cookie": f"{HTTPS_ANONYMOUS_CSRF_COOKIE}={csrf_value}",
        },
        data={
            "email": "admin@example.com",
            "password": PASSWORD,
            "csrf_token": csrf_value,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    session_cookie = response.headers["set-cookie"]
    assert session_cookie.startswith(f"{HTTPS_SESSION_COOKIE}=")
    assert "Secure" in session_cookie and "Domain=" not in session_cookie


def test_untrusted_peer_cannot_use_forwarded_proto_to_enable_https():
    _, client = configured_application()
    untrusted = TestClient(
        client.app,
        base_url="http://community.example",
        client=("192.0.2.1", 1234),
    )
    response = untrusted.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 403
    assert "Set-Cookie" not in response.headers
