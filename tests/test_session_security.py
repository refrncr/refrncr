from __future__ import annotations

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from refrncr.app import create_application, create_community_services
from refrncr.browser_security import (
    HTTPS_ANONYMOUS_CSRF_COOKIE,
    HTTPS_SESSION_COOKIE,
    LOOPBACK_ANONYMOUS_CSRF_COOKIE,
    LOOPBACK_SESSION_COOKIE,
    is_loopback_host,
)
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import LoginSession
from refrncr.services import AuthenticatedPrincipal, ResourceTypeRegistry, SessionService
from refrncr.web import create_flask_app

PASSWORD = "correct horse battery staple"


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def configured_web(*, allow_loopback_http: bool = False):
    engine = create_database_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    app = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        allow_loopback_http=allow_loopback_http,
    )
    return factory, services, app.test_client()


def csrf(response) -> str:
    body = response.get_data(as_text=True)
    marker = "name='csrf_token' value='"
    return body.split(marker, 1)[1].split("'", 1)[0]


def create_admin(client, base_url: str):
    token = csrf(client.get("/setup", base_url=base_url))
    response = client.post(
        "/setup",
        base_url=base_url,
        data={
            "email": "admin@example.com",
            "display_name": "Administrator",
            "password": PASSWORD,
            "csrf_token": token,
        },
    )
    assert response.status_code == 302


def log_in(client, base_url: str):
    token = csrf(client.get("/login", base_url=base_url))
    return client.post(
        "/login",
        base_url=base_url,
        data={"email": "admin@example.com", "password": PASSWORD, "csrf_token": token},
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "[::1]", "localhost"])
def test_loopback_detection_is_literal_and_narrow(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host", ["example.com", "0.0.0.0", "127.1", "localhost.example", "127.0.0.1.evil"]
)
def test_loopback_detection_rejects_non_loopback_hosts(host):
    assert not is_loopback_host(host)


def test_direct_loopback_http_uses_non_secure_host_only_cookies():
    _, _, client = configured_web(allow_loopback_http=True)
    base_url = "http://127.0.0.1"

    setup_form = client.get("/setup", base_url=base_url)
    anonymous = setup_form.headers["Set-Cookie"]
    assert anonymous.startswith(f"{LOOPBACK_ANONYMOUS_CSRF_COOKIE}=")
    assert "HttpOnly" in anonymous and "SameSite=Lax" in anonymous and "Path=/" in anonymous
    assert "Secure" not in anonymous and "Domain=" not in anonymous

    create_admin(client, base_url)
    response = log_in(client, base_url)
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith(f"{LOOPBACK_SESSION_COOKIE}=")
    assert "Max-Age=43200" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie and "Domain=" not in cookie and "__Host-" not in cookie


def test_https_uses_host_prefixed_secure_cookies():
    _, _, client = configured_web()
    base_url = "https://community.example"
    anonymous = client.get("/setup", base_url=base_url).headers["Set-Cookie"]
    assert anonymous.startswith(f"{HTTPS_ANONYMOUS_CSRF_COOKIE}=")
    assert "Secure" in anonymous

    create_admin(client, base_url)
    cookie = log_in(client, base_url).headers["Set-Cookie"]
    assert cookie.startswith(f"{HTTPS_SESSION_COOKIE}=")
    assert all(value in cookie for value in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"))
    assert "Max-Age=43200" in cookie and "Domain=" not in cookie


def test_non_loopback_http_and_forwarded_headers_fail_closed():
    _, services, client = configured_web(allow_loopback_http=True)
    response = client.get(
        "/login",
        base_url="http://community.example",
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
        headers={
            "Forwarded": "for=127.0.0.1;host=localhost;proto=https",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Host": "localhost",
            "X-Forwarded-Proto": "https",
        },
    )
    assert response.status_code == 403
    assert "Set-Cookie" not in response.headers
    assert not services.users.has_users()


def test_loopback_http_requires_both_explicit_enablement_and_direct_loopback_peer():
    _, _, disabled_client = configured_web()
    assert disabled_client.get("/login", base_url="http://127.0.0.1").status_code == 403

    _, _, remote_client = configured_web(allow_loopback_http=True)
    response = remote_client.get(
        "/login",
        base_url="http://127.0.0.1",
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert response.status_code == 403


def test_trusted_proxy_composition_disables_loopback_http_even_with_rewritten_host():
    _, services, _ = configured_web()
    client = TestClient(
        create_application(
            services.users,
            services.authentication,
            services.sessions,
            services.teams,
            services.memberships,
            authorization=services.authorization,
            datasets=services.datasets,
            dataset_fields=services.dataset_fields,
            dataset_business_keys=services.dataset_business_keys,
            allow_loopback_http=True,
            trusted_proxies=("127.0.0.1",),
        ),
        base_url="http://127.0.0.1",
    )

    response = client.get(
        "/login",
        headers={
            "Host": "127.0.0.1",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Host": "127.0.0.1",
            "X-Forwarded-Proto": "http",
        },
    )
    assert response.status_code == 403
    assert "Set-Cookie" not in response.headers


def test_trusted_proxy_composition_accepts_https_with_host_prefixed_cookie():
    _, services, _ = configured_web()
    client = TestClient(
        create_application(
            services.users,
            services.authentication,
            services.sessions,
            services.teams,
            services.memberships,
            authorization=services.authorization,
            datasets=services.datasets,
            dataset_fields=services.dataset_fields,
            dataset_business_keys=services.dataset_business_keys,
            allow_loopback_http=True,
            trusted_proxies=("127.0.0.1",),
        ),
        base_url="https://community.example",
    )

    response = client.get("/login")
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith(f"{HTTPS_ANONYMOUS_CSRF_COOKIE}=")
    assert "Secure" in cookie and "Path=/" in cookie and "Domain=" not in cookie


def test_oversized_setup_body_is_rejected_at_http_boundary():
    _, services, client = configured_web()
    services.users.create_initial_admin = pytest.fail
    response = client.post(
        "/setup",
        base_url="https://community.example",
        data={"email": "A" * 2_000_000},
    )
    assert response.status_code == 413


def test_unknown_fixation_token_is_replaced_and_never_persisted():
    factory, _, client = configured_web()
    base_url = "https://community.example"
    create_admin(client, base_url)
    attacker_token = "attacker-supplied-session-token"
    client.set_cookie(HTTPS_SESSION_COOKIE, attacker_token, domain="community.example", secure=True)

    assert log_in(client, base_url).status_code == 302
    issued = client.get_cookie(HTTPS_SESSION_COOKIE, domain="community.example").value
    assert issued != attacker_token
    with factory() as session:
        hashes = set(session.scalars(select(LoginSession.token_hash)))
    assert hashlib.sha256(attacker_token.encode()).hexdigest() not in hashes
    assert hashlib.sha256(issued.encode()).hexdigest() in hashes


def test_multiple_browser_sessions_remain_valid_and_relogin_replaces_only_presented_session():
    _, services, first = configured_web()
    second = first.application.test_client()
    base_url = "https://community.example"
    create_admin(first, base_url)
    assert log_in(first, base_url).status_code == 302
    first_token = first.get_cookie(HTTPS_SESSION_COOKIE, domain="community.example").value
    assert log_in(second, base_url).status_code == 302
    second_token = second.get_cookie(HTTPS_SESSION_COOKIE, domain="community.example").value

    assert log_in(first, base_url).status_code == 302
    replacement = first.get_cookie(HTTPS_SESSION_COOKIE, domain="community.example").value
    assert services.sessions.resolve(first_token) is None
    assert services.sessions.resolve(second_token) is not None
    assert services.sessions.resolve(replacement) is not None


def test_unauthenticated_logout_does_not_emit_cookie_deletion():
    _, _, client = configured_web()
    response = client.post("/logout", base_url="https://community.example", data={})
    assert response.status_code == 302
    assert "Set-Cookie" not in response.headers


def test_stale_authenticated_cookie_is_cleared_when_presented():
    _, services, client = configured_web()
    base_url = "https://community.example"
    create_admin(client, base_url)
    log_in(client, base_url)
    token = client.get_cookie(HTTPS_SESSION_COOKIE, domain="community.example").value
    services.sessions.revoke(token)

    response = client.get("/users", base_url=base_url)
    assert response.status_code == 302
    deletion = response.headers["Set-Cookie"]
    assert deletion.startswith(f"{HTTPS_SESSION_COOKIE}=")
    assert "Max-Age=0" in deletion and "Secure" in deletion


def test_expired_authenticated_cookie_is_rejected_and_cleared_with_injected_clock():
    factory, services, _ = configured_web()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=UTC))
    sessions = SessionService(factory, clock=clock)
    client = create_flask_app(
        services.users,
        services.authentication,
        sessions,
        services.teams,
        services.memberships,
    ).test_client()
    base_url = "https://community.example"
    create_admin(client, base_url)
    log_in(client, base_url)

    clock.value += timedelta(hours=12)
    response = client.get("/users", base_url=base_url)
    assert response.status_code == 302
    assert "Max-Age=0" in response.headers["Set-Cookie"]


def assert_session_lifecycle_and_cleanup(factory):
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=UTC))
    service = SessionService(factory, clock=clock, cleanup_batch_size=2)
    principal = AuthenticatedPrincipal(str(uuid.uuid4()))
    with factory.begin() as session:
        from refrncr.models import User

        session.add(
            User(
                id=principal.id,
                email="session@example.com",
                display_name="Session",
                password_hash="unused",
                role="user",
            )
        )

    token, login = service.create(principal)
    assert login.expires_at == clock.value + timedelta(hours=12)
    assert service.resolve(token).id == principal.id
    clock.value += timedelta(hours=12)
    assert service.resolve(token) is None

    with factory.begin() as session:
        for index in range(3):
            session.add(
                LoginSession(
                    user_id=principal.id,
                    token_hash=f"{index:064d}",
                    csrf_token="csrf",
                    created_at=clock.value - timedelta(days=9),
                    expires_at=clock.value - timedelta(days=8),
                )
            )
    assert service.cleanup() <= 2
    assert service.cleanup() <= 2
    with factory() as session:
        assert session.scalar(select(func.count(LoginSession.id))) == 1


def test_sqlite_absolute_expiry_and_bounded_retention_cleanup():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    assert_session_lifecycle_and_cleanup(create_session_factory(engine))


def retention_service(*, batch_size=2):
    engine = create_database_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    clock = MutableClock(datetime(2026, 2, 1, tzinfo=UTC))
    principal = AuthenticatedPrincipal(str(uuid.uuid4()))
    with factory.begin() as session:
        from refrncr.models import User

        session.add(
            User(
                id=principal.id,
                email="retention@example.com",
                display_name="Retention",
                password_hash="unused",
                role="user",
            )
        )
    return (
        factory,
        clock,
        principal,
        SessionService(factory, clock=clock, cleanup_batch_size=batch_size),
    )


def add_login(factory, principal, identifier, *, expires_at, revoked_at=None):
    with factory.begin() as session:
        session.add(
            LoginSession(
                id=identifier,
                user_id=principal.id,
                token_hash=hashlib.sha256(identifier.encode()).hexdigest(),
                csrf_token="csrf",
                created_at=expires_at - timedelta(hours=12),
                expires_at=expires_at,
                revoked_at=revoked_at,
            )
        )


def test_retention_boundaries_delete_expired_and_revoked_but_not_active_or_recent_rows():
    factory, clock, principal, service = retention_service(batch_size=10)
    cutoff = clock.value - timedelta(days=7)
    rows = {
        "expired-boundary": (cutoff, None),
        "revoked-boundary": (clock.value + timedelta(days=1), cutoff),
        "recently-expired": (cutoff + timedelta(seconds=1), None),
        "recently-revoked": (clock.value + timedelta(days=1), cutoff + timedelta(seconds=1)),
        "active": (clock.value + timedelta(days=1), None),
    }
    for index, (_name, values) in enumerate(rows.items()):
        add_login(
            factory,
            principal,
            f"{index:036d}",
            expires_at=values[0],
            revoked_at=values[1],
        )

    assert service.cleanup() == 2
    with factory() as session:
        remaining_ids = set(session.scalars(select(LoginSession.id)))
    assert remaining_ids == {f"{index:036d}" for index in (2, 3, 4)}


def test_cleanup_pages_wrap_and_progress_during_continued_insertion():
    factory, clock, principal, service = retention_service(batch_size=2)
    expired = clock.value - timedelta(days=8)
    for index in range(6):
        add_login(factory, principal, f"a{index:035d}", expires_at=expired)

    service.cleanup()
    add_login(factory, principal, f"0{0:035d}", expires_at=expired)
    for index in range(6):
        service.cleanup()
        add_login(
            factory,
            principal,
            f"z{index:035d}",
            expires_at=clock.value + timedelta(days=1),
        )

    with factory() as session:
        remaining_ids = set(session.scalars(select(LoginSession.id)))
    assert remaining_ids == {f"z{index:035d}" for index in range(6)}


def test_concurrent_cleanup_callers_each_make_serialized_bounded_progress():
    factory, clock, principal, service = retention_service(batch_size=2)
    for index in range(8):
        add_login(
            factory,
            principal,
            f"a{index:035d}",
            expires_at=clock.value - timedelta(days=8),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        deleted = list(executor.map(lambda _index: service.cleanup(), range(4)))

    assert all(count <= 2 for count in deleted)
    assert sum(deleted) == 8
    with factory() as session:
        assert session.scalar(select(func.count(LoginSession.id))) == 0


@pytest.mark.postgres
def test_postgresql_session_create_resolve_expire_revoke_rotation_and_cleanup():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"session_security_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        assert_session_lifecycle_and_cleanup(factory)

        clock = MutableClock(datetime(2026, 2, 1, tzinfo=UTC))
        service = SessionService(factory, clock=clock)
        with factory() as session:
            user_id = session.scalar(select(LoginSession.user_id).limit(1))
        principal = AuthenticatedPrincipal(user_id)
        first, _ = service.create(principal)
        second, _ = service.create(principal)
        service.revoke(first)
        assert service.resolve(first) is None
        assert service.resolve(second) is not None
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration_engine.dispose()
