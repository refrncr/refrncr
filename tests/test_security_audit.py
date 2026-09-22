"""REF-31 durable security outcomes and secret-free presentation."""

from sqlalchemy import select
from test_audit_coverage import audit_db as audit_db
from test_login_throttling import HTTPS, LoginAttemptLimiter, configured_web, post_login

from refrncr.audit import record_security_event, validate_event
from refrncr.audit_presentation import present_event
from refrncr.models import AuditEvent
from refrncr.web import SESSION_COOKIE


def test_security_event_catalog_is_content_free(audit_db):
    _engine, sessions, services = audit_db
    admin = services.users.create_initial_admin(
        "admin@example.com", "Admin", "correct horse battery staple"
    )
    actions = (
        ("security.login_failed", None),
        ("security.session_rejected", None),
        ("security.authorization_denied", admin.id),
        ("security.csrf_rejected", admin.id),
        ("security.workflow_token_rejected", admin.id),
    )
    for action, actor in actions:
        record_security_event(services.users.uow, action, actor_user_id=actor)
    with sessions() as session:
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.object_type == "security"))
        )
    assert {event.action for event in events} == {action for action, _actor in actions}
    assert all(event.details is None for event in events)
    assert all(
        validate_event(
            actor_user_id=event.actor_user_id,
            object_type=event.object_type,
            object_id=event.object_id,
            action=event.action,
        )
        is None
        for event in events
    )
    assert {
        present_event(event, {}, {}).actor_label for event in events if event.actor_user_id is None
    } == {"Unauthenticated"}


def test_failed_login_and_admin_denial_are_audited_without_secrets():
    services, client = configured_web(LoginAttemptLimiter())
    password = "representative-password-secret"
    response = post_login(client, "unknown-secret@example.com", password)
    assert response.status_code == 401

    response = post_login(client, "admin@example.com", "correct horse battery staple")
    assert response.status_code == 302
    user = services.users.create_user(
        services.authentication.authenticate(
            "admin@example.com", "correct horse battery staple"
        ).id,
        "ordinary@example.com",
        "Ordinary",
        "correct horse battery staple",
    )
    token, _session = services.sessions.create(user)
    client.set_cookie(SESSION_COOKIE, token, domain="community.example")
    assert client.get("/settings/audit-log", base_url=HTTPS).status_code == 403

    with services.users.uow.sessions() as session:
        events = list(session.scalars(select(AuditEvent)))
    stored = repr([event.__dict__ for event in events])
    assert "security.login_failed" in stored
    assert "security.authorization_denied" in stored
    assert password not in stored
    assert "unknown-secret@example.com" not in stored
