"""REF-40 lifecycle, secrecy, ownership, current authority and concurrency."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from refrncr.api_key_persistence import SqlAlchemyApiKeyOperations
from refrncr.api_keys import LAST_USED_INTERVAL, LIFETIME, ApiKeyAuthenticationError, ApiKeyService
from refrncr.audit import AuditContractError, validate_event
from refrncr.audit_presentation import present_event
from refrncr.models import ApiKey, AuditEvent
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import AuthorizationError, ConflictError, NotFoundError, ValidationError
from refrncr.web import SESSION_COOKIE, create_flask_app


@pytest.fixture
def keys(audit_db):
    engine, sessions, services = audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = services.users.create_user(admin.id, "user@example.com", "User", "valid password")
    now = [datetime(2026, 9, 19, 12, tzinfo=UTC)]
    service = ApiKeyService(
        services.users.uow, pepper=b"test-only-pepper!" * 2, clock=lambda: now[0]
    )
    return engine, sessions, services, admin, user, service, now


def test_lifecycle_and_secret_redaction(keys, caplog):
    _, sessions, _, admin, _, service, now = keys
    created = service.create_key(admin.id, "  Integration  ")
    assert created.key.name == "Integration"
    assert created.key.expires_at == now[0] + LIFETIME
    assert created.secret not in repr(created)
    assert len(created.secret.split(".")[1]) == 43
    assert service.authenticate("Bearer " + created.secret).id == admin.id
    assert service.list_keys(admin.id)[0].last_used_at == now[0]
    with sessions() as session:
        stored = session.get(ApiKey, created.key.id)
        assert stored.verifier != created.secret
        verifier = stored.verifier
        raw_database = repr(
            [
                tuple(row)
                for table in ApiKey.metadata.sorted_tables
                for row in session.execute(select(table))
            ]
        )
        assert created.secret not in raw_database
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.object_type == "api_key"))
        )
        assert {e.action for e in events} == {"api_key.created"}
        assert all(e.details is None for e in events)
        assert created.secret not in repr([e.__dict__ for e in events])
    service.revoke_key(admin.id, created.key.id, 1, confirmed=True)
    assert service.list_keys(admin.id)[0].status == "Revoked"
    with pytest.raises(ApiKeyAuthenticationError, match="Invalid API credentials"):
        service.authenticate("Bearer " + created.secret)
    with pytest.raises(ConflictError):
        service.revoke_key(admin.id, created.key.id, 2, confirmed=True)
    assert created.secret not in caplog.text and verifier not in caplog.text


@pytest.mark.parametrize("value", [None, "", " ", "x" * 101, 1])
def test_name_validation(keys, value):
    *_, admin, user, service, now = keys
    with pytest.raises(ValidationError):
        service.create_key(user.id, value)


@pytest.mark.parametrize("delta", [-1, 0, 91])
def test_expiry_bounds(keys, delta):
    _, _, _, _, user, service, now = keys
    with pytest.raises(ValidationError):
        service.create_key(user.id, "Expiry", now[0] + timedelta(days=delta))


@pytest.mark.parametrize("value", ["", "forever", "2026-12-01", datetime(2026, 12, 1), 123])
def test_malformed_expiry(keys, value):
    _, _, _, _, user, service, _ = keys
    with pytest.raises(ValidationError):
        service.create_key(user.id, "Expiry", value)


def test_expiry_duplicate_names_limit_and_reuse(keys):
    _, _, _, _, user, service, now = keys
    first = service.create_key(user.id, "Integration", now[0] + timedelta(days=1))
    with pytest.raises(ConflictError):
        service.create_key(user.id, "integration")
    for n in range(4):
        service.create_key(user.id, str(n))
    with pytest.raises(ConflictError):
        service.create_key(user.id, "Sixth")
    now[0] += timedelta(days=1)
    assert service.get_key(user.id, first.key.id).status == "Expired"
    with pytest.raises(ApiKeyAuthenticationError):
        service.authenticate("Bearer " + first.secret)
    replacement = service.create_key(user.id, "Integration")
    assert replacement.secret != first.secret
    service.revoke_key(user.id, replacement.key.id, 1, confirmed=True)
    assert service.create_key(user.id, "Integration").key.status == "Active"


def test_owner_isolation_and_confirmation(keys):
    _, _, _, admin, user, service, _ = keys
    created = service.create_key(user.id, "Private name")
    assert service.list_keys(admin.id) == ()
    for target in (created.key.id, str(uuid4()), "malformed", None):
        with pytest.raises(NotFoundError, match="API key not found"):
            service.get_key(admin.id, target)
        with pytest.raises(NotFoundError, match="API key not found"):
            service.revoke_key(admin.id, target, 1, confirmed=True)
    with pytest.raises(ValidationError):
        service.revoke_key(user.id, created.key.id, 1)
    with pytest.raises(ConflictError):
        service.revoke_key(user.id, created.key.id, 2, confirmed=True)
    assert service.authenticate("Bearer " + created.secret).id == user.id


@pytest.mark.parametrize("state", ["missing", "inactive", "deleted"])
def test_ineligible_actors(keys, state):
    _, _, s, admin, user, service, _ = keys
    created = service.create_key(user.id, "Test")
    actor = None if state == "missing" else user.id
    if state == "inactive":
        s.users.disable_user(admin.id, user.id)
    elif state == "deleted":
        s.users.delete_user(admin.id, user.id)
    for operation in (
        lambda: service.create_key(actor, "Denied"),
        lambda: service.list_keys(actor),
        lambda: service.get_key(actor, created.key.id),
        lambda: service.revoke_key(actor, created.key.id, 1, confirmed=True),
    ):
        with pytest.raises(AuthorizationError):
            operation()
    if state != "missing":
        with pytest.raises(ApiKeyAuthenticationError):
            service.authenticate("Bearer " + created.secret)


@pytest.mark.parametrize(
    "value",
    [None, "", "Basic token", "Bearer unknown", "Bearer " + "x" * 10000, [], "Bearer \N{SNOWMAN}"],
)
def test_uniform_failure_and_anonymous_audit(keys, value):
    _, sessions, _, _, _, service, _ = keys
    with pytest.raises(ApiKeyAuthenticationError) as failure:
        service.authenticate(value)
    assert failure.value.status_code == 401
    assert str(failure.value) == "Invalid API credentials"
    with sessions() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "api_key.authentication_failed")
        )
        assert event.actor_user_id is None and event.details is None
        assert present_event(event, {}, {}).actor_label == "Unauthenticated"
        validate_event(
            actor_user_id=None,
            object_type="api_key",
            object_id=event.object_id,
            action=event.action,
        )
        with pytest.raises(AuditContractError):
            validate_event(
                actor_user_id=None,
                object_type="api_key",
                object_id=event.object_id,
                action="api_key.created",
            )


def test_current_permissions_and_bearer_only(keys):
    _, _, s, admin, user, service, _ = keys
    team = s.teams.create_team(admin.id, "Owners")
    dataset = s.datasets.create_dataset(admin.id, "keys", "Keys", "", team.id)
    key = service.create_key(user.id, "Integration")
    actor = service.authenticate("Bearer " + key.secret)
    assert not s.authorization.datasets.can_maintain_dataset(actor.id, dataset.id)
    membership = s.memberships.create_membership(admin.id, user.id, team.id)
    assert s.authorization.datasets.can_maintain_dataset(actor.id, dataset.id)
    s.memberships.delete_membership(admin.id, team.id, membership.id)
    assert not s.authorization.datasets.can_maintain_dataset(actor.id, dataset.id)
    for value in (
        key.secret,
        "Basic " + key.secret,
        "Bearer  " + key.secret,
        "Bearer " + key.secret + "\n",
    ):
        with pytest.raises(ApiKeyAuthenticationError):
            service.authenticate(value)


@pytest.mark.parametrize("duplicate", [False, True])
def test_concurrent_creation(keys, duplicate):
    _, _, _, _, user, service, _ = keys
    for n in range(4 if not duplicate else 0):
        service.create_key(user.id, str(n))
    barrier = Barrier(2)

    def create(index):
        barrier.wait(timeout=10)
        try:
            return service.create_key(user.id, "Same" if duplicate else f"Race {index}")
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))
    assert sum(item is not None for item in results) == 1
    assert len(service.list_keys(user.id)) == (1 if duplicate else 5)


def test_creation_replay_and_concurrent_revoke(keys):
    _, sessions, _, _, user, service, _ = keys
    request_id = str(uuid4())
    key = service.create_key(user.id, "Key", request_id=request_id)
    with pytest.raises(ConflictError):
        service.create_key(user.id, "Replay", request_id=request_id)
    barrier = Barrier(2)

    def revoke(_):
        barrier.wait(timeout=10)
        try:
            service.revoke_key(user.id, key.key.id, 1, confirmed=True)
            return True
        except ConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(revoke, range(2))) == 1
    with sessions() as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(AuditEvent).where(AuditEvent.action == "api_key.revoked")
                    )
                )
            )
            == 1
        )


def test_audit_failure_rolls_back(keys, monkeypatch):
    _, _, _, _, user, service, _ = keys
    original = SqlAlchemyUnitOfWork.add_audit

    def fail(work, **values):
        original(work, **values)
        work.flush()
        raise RuntimeError("test audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    with pytest.raises(RuntimeError, match="test audit failure"):
        service.create_key(user.id, "Rollback")
    assert service.list_keys(user.id) == ()


def browser_client(keys):
    _, _, s, _, user, service, now = keys
    now[0] = datetime.now(UTC)
    app = create_flask_app(
        s.users, s.authentication, s.sessions, s.teams, s.memberships, api_keys=service
    )
    token, login = s.sessions.create(user)
    client = app.test_client()
    client.set_cookie(SESSION_COOKIE, token, domain="localhost")
    return client, login.csrf_token


def hidden(body, name):
    return re.search(r'name="' + name + r'" value="([^"]+)"', body).group(1)


def test_browser_creation_refresh_and_csrf(keys):
    client, csrf = browser_client(keys)
    get = client.get("/settings/api-keys/new", base_url="https://localhost")
    body = get.get_data(as_text=True)
    data = dict(
        csrf_token=csrf,
        creation_token=hidden(body, "creation_token"),
        name="<script>integration</script>",
        expires_at=(datetime.now(UTC) + LIFETIME).date().isoformat(),
    )
    assert (
        client.post(
            "/settings/api-keys/new",
            data=data | {"csrf_token": "wrong"},
            base_url="https://localhost",
        ).status_code
        == 403
    )
    response = client.post("/settings/api-keys/new", data=data, base_url="https://localhost")
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    html = response.get_data(as_text=True)
    secret = re.search(r"rfr_[0-9a-f]{32}\.[A-Za-z0-9_-]{43}", html).group()
    assert "<script>integration</script>" not in html
    assert "Copy your secret now" in html and "<noscript>" in html
    repeated = client.post("/settings/api-keys/new", data=data, base_url="https://localhost")
    assert repeated.status_code == 400 and secret not in repeated.get_data(as_text=True)
    listing = client.get("/settings/api-keys", base_url="https://localhost")
    assert secret not in listing.get_data(as_text=True)
    _, _, _, _, user, service, _ = keys
    assert len(service.list_keys(user.id)) == 1


def test_unknown_wrong_secret_and_unicode_fail_closed(keys):
    _, _, _, _, user, service, _ = keys
    key = service.create_key(user.id, "Original")
    failures = [
        "Bearer rfr_" + uuid4().hex + "." + "a" * 43,
        "Bearer " + key.secret[:-43] + "a" * 43,
        "Bearer " + key.secret[:-43] + "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}" * 43,
    ]
    for value in failures:
        with pytest.raises(ApiKeyAuthenticationError) as failure:
            service.authenticate(value)
        assert failure.value.body == {"error": "invalid_api_credentials"}
        assert value not in str(failure.value)
        assert service.get_key(user.id, key.key.id).last_used_at is None


def test_corrupt_verifier_and_pepper_rotation_fail_closed(keys):
    _, sessions, s, _, user, service, _ = keys
    key = service.create_key(user.id, "Original")
    rotated = ApiKeyService(s.users.uow, pepper=b"different-test-only-pepper-value!!")
    with pytest.raises(ApiKeyAuthenticationError):
        rotated.authenticate("Bearer " + key.secret)
    with sessions.begin() as session:
        session.get(ApiKey, key.key.id).verifier = "\N{SNOWMAN}" * 64
    with pytest.raises(ApiKeyAuthenticationError):
        service.authenticate("Bearer " + key.secret)


def test_required_pepper_configuration(keys, monkeypatch):
    from refrncr.api_keys import configured_api_keys

    _, _, s, _, _, _, _ = keys
    monkeypatch.delenv("REFRNCR_API_KEY_PEPPER", raising=False)
    assert configured_api_keys(s.users.uow) is None
    for value in ("", "short", "x" * 64):
        monkeypatch.setenv("REFRNCR_API_KEY_PEPPER", value)
        with pytest.raises(ValueError):
            configured_api_keys(s.users.uow)
    monkeypatch.setenv("REFRNCR_API_KEY_PEPPER", "ab" * 32)
    assert isinstance(configured_api_keys(s.users.uow), ApiKeyService)
    for value in (None, b"", b"a" * 31, "a" * 64):
        with pytest.raises(ValueError):
            ApiKeyService(s.users.uow, pepper=value)


def test_browser_revoke_validation_idor_and_api_key_cannot_login(keys):
    client, csrf = browser_client(keys)
    _, _, s, admin, user, service, _ = keys
    own = service.create_key(user.id, "Own")
    other = service.create_key(admin.id, "Private")
    base = "https://localhost"
    path = "/settings/api-keys/" + own.key.id + "/revoke"
    response = client.get(path, base_url=base)
    assert 'class="api-key-revoke-form"' in response.get_data(as_text=True)
    assert client.post(path, data={"csrf_token": "wrong"}, base_url=base).status_code == 403
    response = client.post(path, data={"csrf_token": csrf, "revision": "1"}, base_url=base)
    assert response.status_code == 400
    assert service.get_key(user.id, own.key.id).status == "Active"
    response = client.post(
        path, data={"csrf_token": csrf, "revision": "1", "confirm": "yes"}, base_url=base
    )
    assert response.status_code == 303
    for target in (other.key.id, str(uuid4())):
        response = client.get("/settings/api-keys/" + target + "/revoke", base_url=base)
        assert response.status_code == 404
        assert response.get_data(as_text=True) == "API key not found"
    client.delete_cookie(SESSION_COOKIE, domain="localhost")
    response = client.get(
        "/settings/api-keys", headers={"Authorization": "Bearer " + other.secret}, base_url=base
    )
    assert response.status_code == 302 and "/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_at", ""),
        ("expires_at", "forever"),
        ("expires_at", "9999-12-31"),
        ("expires_at", "2000-01-01"),
        ("expires_at", "2026-02-30"),
        ("creation_token", "a" * 36 + "." + "\N{SNOWMAN}" * 64),
        ("creation_token", str(uuid4()) + "." + "0" * 64),
    ],
)
def test_browser_invalid_fields_fail_safely(keys, field, value):
    client, csrf = browser_client(keys)
    body = client.get("/settings/api-keys/new", base_url="https://localhost").get_data(as_text=True)
    data = dict(
        csrf_token=csrf,
        creation_token=hidden(body, "creation_token"),
        name="Test",
        expires_at=(datetime.now(UTC) + LIFETIME).date().isoformat(),
    )
    data[field] = value
    response = client.post("/settings/api-keys/new", data=data, base_url="https://localhost")
    assert response.status_code == 400
    assert "id='key-error'" in response.get_data(as_text=True)
    _, _, _, _, user, service, _ = keys
    assert service.list_keys(user.id) == ()


def test_secret_copy_controller():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["node", "tests/javascript/api_key_secret.cjs", "src/refrncr/static/js/api-key-secret.js"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_audit_failure_rolls_back_revoke_and_use(keys, monkeypatch):
    _, sessions, _, _, user, service, _ = keys
    created = service.create_key(user.id, "Preserve")
    with sessions() as session:
        before = list(session.execute(select(AuditEvent.id)))
    original = SqlAlchemyUnitOfWork.add_audit

    def fail(work, **values):
        original(work, **values)
        work.flush()
        raise RuntimeError("test audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    with pytest.raises(RuntimeError, match="test audit failure"):
        service.revoke_key(user.id, created.key.id, 1, confirmed=True)
    persisted = service.get_key(user.id, created.key.id)
    assert persisted.status == "Active" and persisted.revision == 1
    assert persisted.last_used_at is None
    with sessions() as session:
        assert list(session.execute(select(AuditEvent.id))) == before


def test_success_has_no_durable_audit_and_throttles_last_used(keys):
    _, sessions, _, _, user, service, now = keys
    created = service.create_key(user.id, "Recent use")

    assert service.authenticate("Bearer " + created.secret).id == user.id
    first_use = now[0]
    now[0] += LAST_USED_INTERVAL
    service.authenticate("Bearer " + created.secret)
    assert service.get_key(user.id, created.key.id).last_used_at == first_use

    now[0] += timedelta(microseconds=1)
    service.authenticate("Bearer " + created.secret)
    assert service.get_key(user.id, created.key.id).last_used_at == now[0]
    with sessions() as session:
        assert not list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == "api_key.authenticated"))
        )


def test_last_used_update_failure_rolls_back_and_fails_closed(keys, monkeypatch):
    _, _, _, _, user, service, _ = keys
    created = service.create_key(user.id, "Rollback use")

    def fail(*args, **kwargs):
        raise RuntimeError("test last-used failure")

    monkeypatch.setattr(SqlAlchemyApiKeyOperations, "update_last_used", fail)
    with pytest.raises(RuntimeError, match="test last-used failure"):
        service.authenticate("Bearer " + created.secret)
    assert service.get_key(user.id, created.key.id).last_used_at is None


def test_concurrent_authentication_refreshes_last_used_once(keys, monkeypatch):
    _, _, _, _, user, service, _ = keys
    created = service.create_key(user.id, "Concurrent use")
    barrier = Barrier(2)
    outcomes = []
    original = SqlAlchemyApiKeyOperations.update_last_used

    def observe(operation, *args, **kwargs):
        updated = original(operation, *args, **kwargs)
        outcomes.append(updated)
        return updated

    monkeypatch.setattr(SqlAlchemyApiKeyOperations, "update_last_used", observe)

    def authenticate(_):
        barrier.wait(timeout=10)
        return service.authenticate("Bearer " + created.secret)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert [principal.id for principal in executor.map(authenticate, range(2))] == [
            user.id,
            user.id,
        ]
    assert sorted(outcomes) == [False, True]


def test_authentication_waits_for_committing_revoke(keys, monkeypatch):
    _, _, _, _, user, service, _ = keys
    created = service.create_key(user.id, "Ordered revoke")
    revoking, authenticating, release = Event(), Event(), Event()
    audit = service._audit

    def pause_revoke(work, actor_id, object_id, action):
        audit(work, actor_id, object_id, action)
        if action == "api_key.revoked":
            revoking.set()
            assert release.wait(timeout=10)

    monkeypatch.setattr(service, "_audit", pause_revoke)

    def authenticate():
        authenticating.set()
        with pytest.raises(ApiKeyAuthenticationError):
            service.authenticate("Bearer " + created.secret)

    with ThreadPoolExecutor(max_workers=2) as executor:
        revoke = executor.submit(service.revoke_key, user.id, created.key.id, 1, confirmed=True)
        try:
            assert revoking.wait(timeout=10)
            authentication = executor.submit(authenticate)
            assert authenticating.wait(timeout=10)
        finally:
            release.set()
        revoke.result(timeout=10)
        authentication.result(timeout=10)
    assert service.get_key(user.id, created.key.id).status == "Revoked"
