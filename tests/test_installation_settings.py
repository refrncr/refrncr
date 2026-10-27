"""Installation access policies, transition confirmation and dataset Access UI."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select
from support import dataset_version_client, html_tag_texts, publish, request, setup

from refrncr.installation_policy import EXPORT_POLICIES
from refrncr.installation_settings import PolicyConfirmationRequired
from refrncr.models import AuditEvent
from refrncr.persistence import SqlAlchemyInstallationSettingsOperations, SqlAlchemyUnitOfWork
from refrncr.services import AuthorizationError, ConflictError, NotFoundError, ValidationError


def set_policy(c, policy):
    c.service = c.s.dataset_versions
    settings = c.s.installation_settings
    return settings.change_policy(c.a.id, settings.get_policy(c.a.id), policy, confirmed=True)


def events(c):
    with c.sessions() as session:
        return list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.object_type == "installation_settings")
            )
        )


@pytest.mark.parametrize("policy", EXPORT_POLICIES)
@pytest.mark.parametrize(
    "role", ["admin", "owner", "other", "no_team", "user_grant", "team_grant", "inactive"]
)
def test_access_policy_capabilities(installation_context, policy, role):
    c = installation_context
    setup(c)
    publish(c)
    assert c.s.installation_settings.get_policy(c.a.id) == "everyone"
    set_policy(c, "admin_grants")
    grant = c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    c.s.datasets.revoke_export_access(c.a.id, c.d.key, grant.id)
    actor = c.a if role == "admin" else c.u
    if role == "owner":
        c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    if role in {"other", "team_grant"}:
        c.s.memberships.create_membership(c.a.id, c.u.id, c.other.id)
    if role in {"user_grant", "inactive"}:
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    if role == "team_grant":
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "team", c.other.id)
    if role == "inactive":
        c.s.users.disable_user(c.a.id, c.u.id)
    set_policy(c, policy)
    auth = c.s.authorization.datasets
    active = role != "inactive"
    can_export = active and (policy == "everyone" or role not in {"other", "no_team"})
    can_manage = (
        active
        and policy != "everyone"
        and (role == "admin" or (policy == "team_grants" and role == "owner"))
    )
    assert auth.can_export_dataset(actor.id, c.d.id) == can_export
    assert auth.can_manage_export_access(actor.id, c.d.id) == can_manage
    assert auth.can_change_dataset_owner(actor.id, c.d.id) == (role == "admin")
    assert auth.can_maintain_dataset(actor.id, c.d.id) == (role in {"admin", "owner"})
    if can_export:
        c.s.dataset_records.export_dataset(actor.id, c.d.key, "csv").content.close()
    else:
        with pytest.raises(NotFoundError):
            c.s.dataset_records.export_dataset(actor.id, c.d.key, "csv")
    if can_manage:
        entry = c.s.datasets.grant_export_access(actor.id, c.d.key, "user", c.a.id)
        c.s.datasets.revoke_export_access(actor.id, c.d.key, entry.id)
    else:
        with pytest.raises(NotFoundError):
            c.s.datasets.grant_export_access(actor.id, c.d.key, "user", c.a.id)
        with pytest.raises(NotFoundError):
            c.s.datasets.revoke_export_access(actor.id, c.d.key, grant.id)


TRANSITIONS = [
    ("everyone", "team_grants", "Users outside the owning team"),
    ("everyone", "admin_grants", "Users outside the owning team"),
    ("team_grants", "admin_grants", "Team members will no longer"),
    ("team_grants", "everyone", "Every active user will be able"),
    ("admin_grants", "everyone", "Every active user will be able"),
    ("admin_grants", "team_grants", None),
]


@pytest.mark.parametrize("previous,policy,message", TRANSITIONS)
def test_policy_transition_confirmation_and_audit(installation_context, previous, policy, message):
    c = installation_context
    set_policy(c, previous)
    before = len(events(c))
    browser, csrf = dataset_version_client(c)
    path = "/settings/dataset-access"
    data = {"csrf_token": csrf, "expected_policy": previous, "policy": policy}
    response = request(browser, "post", path, data=data)
    if message:
        assert response.status_code == 200 and message in response.text
        assert "Confirm change" in response.text
        assert c.s.installation_settings.get_policy(c.a.id) == previous
        assert len(events(c)) == before
        response = request(browser, "post", path, data=data | {"confirmed": "yes"})
    assert response.status_code == 303
    assert c.s.installation_settings.get_policy(c.a.id) == policy
    assert len(events(c)) == before + 1
    event = events(c)[-1]
    assert event.actor_user_id == c.a.id
    assert event.action == "export_access_policy_changed"
    assert event.details == {"previous_policy": previous, "policy": policy}
    # Retry/no-op does not create duplicate audit evidence.
    assert request(browser, "post", path, data=data | {"confirmed": "yes"}).status_code == 409
    c.s.installation_settings.change_policy(c.a.id, policy, policy)
    assert len(events(c)) == before + 1


@pytest.mark.parametrize("policy", EXPORT_POLICIES)
@pytest.mark.parametrize("role", ["admin", "owner", "other"])
def test_access_tab_per_policy_and_role(installation_ui_context, policy, role):
    c = installation_ui_context
    setup(c)
    publish(c)
    set_policy(c, "admin_grants")
    entry = c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    if role == "owner":
        c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    set_policy(c, policy)
    browser, csrf = dataset_version_client(c, c.a if role == "admin" else c.u)
    root = f"/datasets/{c.d.key}"
    page = request(browser, "get", root + "?tab=access-management")
    assert page.status_code == 200
    can_view = role not in {"other", "no_team"}
    can_manage = can_view and policy != "everyone" and (role == "admin" or policy == "team_grants")
    assert ("id='access-management'" in page.text) == can_view
    assert ("id='tab-access'" in page.text) == can_view
    assert ("Grant export access" in page.text) == can_manage
    assert ("data-access-entry" in page.text) == (can_view and policy != "everyone")
    assert ("All active users can export this dataset and use the API." in page.text) == (
        can_view and policy == "everyone"
    )
    assert ("Administrators manage access to this dataset." in page.text) == (
        role == "owner" and policy == "admin_grants"
    )
    assert ("Change owning team" in page.text) == (role == "admin")
    if can_view:
        assert c.owner.name in page.text
        access_html = page.text.split("id='access-management'", 1)[1]
        description = html_tag_texts(access_html, "p")[0]
        assert description.startswith("Applies to the whole dataset, not to one version.")
        assert ("Changes take effect right away." in description) == (can_manage or role == "admin")
    if role == "owner" and policy == "admin_grants":
        assert c.u.email in page.text
        assert f"/datasets/{c.d.key}/export-access" not in page.text
    post = request(
        browser,
        "post",
        root + "/export-access",
        data={
            "csrf_token": csrf,
            "subject": "team:" + c.other.id,
        },
    )
    assert post.status_code == (302 if can_manage else 404)
    revoked = request(
        browser, "post", root + f"/export-access/{entry.id}/revoke", data={"csrf_token": csrf}
    )
    assert revoked.status_code == (302 if can_manage else 404)


@pytest.mark.parametrize("policy", EXPORT_POLICIES)
@pytest.mark.parametrize("role", ["owner", "other"])
def test_non_admin_cannot_read_or_post_policy_settings(installation_context, policy, role):
    c = installation_context
    set_policy(c, policy)
    team = c.owner if role == "owner" else c.other
    c.s.memberships.create_membership(c.a.id, c.u.id, team.id)
    before = len(events(c))
    browser, csrf = dataset_version_client(c, c.u)
    path = "/settings/dataset-access"
    assert request(browser, "get", path).status_code == 403
    response = request(
        browser,
        "post",
        path,
        data={
            "csrf_token": csrf,
            "expected_policy": policy,
            "policy": "admin_grants" if policy == "everyone" else "everyone",
            "confirmed": "yes",
        },
    )
    assert response.status_code == 403
    assert c.s.installation_settings.get_policy(c.a.id) == policy
    assert len(events(c)) == before


def test_policy_errors_and_grant_preservation(installation_context, monkeypatch):
    c = installation_context
    settings = c.s.installation_settings
    set_policy(c, "admin_grants")
    grant = c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    before = len(events(c))
    with pytest.raises(AuthorizationError):
        settings.change_policy(c.u.id, "admin_grants", "everyone", confirmed=True)
    with pytest.raises(ValidationError):
        settings.change_policy(c.a.id, "admin_grants", "invalid", confirmed=True)
    with pytest.raises(ConflictError):
        settings.change_policy(c.a.id, "everyone", "team_grants", confirmed=True)
    with pytest.raises(PolicyConfirmationRequired):
        settings.change_policy(c.a.id, "admin_grants", "everyone")
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise RuntimeError("audit unavailable")

        patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
        with pytest.raises(RuntimeError):
            settings.change_policy(c.a.id, "admin_grants", "everyone", confirmed=True)
    assert settings.get_policy(c.a.id) == "admin_grants"
    assert len(events(c)) == before
    set_policy(c, "everyone")
    assert c.s.datasets.export_access_context(c.a.id, c.d.key)[3] == []
    with pytest.raises(NotFoundError):
        c.s.datasets.revoke_export_access(c.a.id, c.d.key, grant.id)
    set_policy(c, "team_grants")
    assert c.s.datasets.export_access_context(c.a.id, c.d.key)[3][0][0].id == grant.id
    browser, _ = dataset_version_client(c)
    assert (
        request(
            browser, "post", "/settings/dataset-access", data={"policy": "everyone"}
        ).status_code
        == 403
    )


def test_concurrent_policy_updates_have_one_winner(installation_context):
    c = installation_context

    def change(policy):
        try:
            c.s.installation_settings.change_policy(c.a.id, "everyone", policy, confirmed=True)
            return True
        except ConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, ("team_grants", "admin_grants"))) == [False, True]
    assert len(events(c)) == 1


def test_policy_change_serializes_with_grant(installation_context, monkeypatch):
    c = installation_context
    set_policy(c, "team_grants")
    c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    changed, release, attempted = Event(), Event(), Event()
    original_change = SqlAlchemyInstallationSettingsOperations.change_policy
    original_get = SqlAlchemyInstallationSettingsOperations.get_policy

    def held_change(self, expected, policy):
        result = original_change(self, expected, policy)
        changed.set()
        assert release.wait(10)
        return result

    def get(self, *, for_update=False):
        if changed.is_set() and for_update:
            attempted.set()
        return original_get(self, for_update=for_update)

    monkeypatch.setattr(SqlAlchemyInstallationSettingsOperations, "change_policy", held_change)
    monkeypatch.setattr(SqlAlchemyInstallationSettingsOperations, "get_policy", get)
    with ThreadPoolExecutor(max_workers=2) as pool:
        policy = pool.submit(
            c.s.installation_settings.change_policy,
            c.a.id,
            "team_grants",
            "admin_grants",
            confirmed=True,
        )
        try:
            assert changed.wait(10)
            grant = pool.submit(
                c.s.datasets.grant_export_access, c.u.id, c.d.key, "team", c.other.id
            )
            # SQLite serializes at BEGIN IMMEDIATE, before get_policy is reached.
            if c.engine.dialect.name == "postgresql":
                assert attempted.wait(10)
            assert not grant.done()
        finally:
            release.set()
        assert policy.result() == "admin_grants"
        with pytest.raises(NotFoundError):
            grant.result()
    assert c.s.datasets.export_access_context(c.a.id, c.d.key)[3] == []
