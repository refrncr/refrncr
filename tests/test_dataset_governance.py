"""REF-73 authority, ownership compare-and-set, and orthogonal export contracts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from dataset_helpers import publish_dataset
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from support import governance_state as state
from support import html_elements

from refrncr.models import AuditEvent, Dataset, DatasetExportAccess, DatasetRecord, Grant
from refrncr.persistence import SqlAlchemyDatasetOperations, SqlAlchemyUnitOfWork
from refrncr.services import (
    AuthorizationError,
    ConflictError,
    InvalidLifecycleError,
    NotFoundError,
    StaleDatasetOwnerError,
    ValidationError,
)
from refrncr.web import create_flask_app


@pytest.mark.parametrize("kind", ["viewer", "export_user", "export_team", "owner", "admin"])
def test_complete_capability_matrix(governance, kind):
    _, factory, s, a, u, owner, other, d = governance
    if kind == "owner":
        s.memberships.create_membership(a.id, u.id, owner.id)
    elif kind == "admin":
        u = a
    elif kind == "export_user":
        s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    elif kind == "export_team":
        s.memberships.create_membership(a.id, u.id, other.id)
        s.datasets.grant_export_access(a.id, d.key, "team", other.id)
    if kind not in {"owner", "admin"}:
        publish_dataset(s, a.id, d)
    auth = s.authorization.datasets
    assert auth.can_view_dataset(u.id, d.id)
    assert auth.can_create_dataset(u.id)
    assert auth.can_create_dataset(u.id, owner.id) == (kind in {"owner", "admin"})
    assert auth.can_maintain_dataset(u.id, d.id) == (kind in {"owner", "admin"})
    assert auth.can_export_dataset(u.id, d.id) == (kind != "viewer")
    assert auth.can_change_dataset_owner(u.id, d.id) == (kind == "admin")
    assert auth.can_manage_export_access(u.id, d.id) == (kind == "admin")
    assert [item.id for item in s.datasets.list_datasets(u.id)] == [d.id]
    assert s.datasets.get_dataset_details(u.id, d.key).owner_team_name == owner.name
    expected_keys = [] if kind in {"owner", "admin"} else ["publication_id"]
    assert [field.key for field in s.dataset_fields.list_fields(u.id, d.key)] == expected_keys
    assert s.dataset_business_keys.get_business_key(u.id, d.key).field_keys == tuple(expected_keys)
    assert s.dataset_records.list_records(u.id, d.key).total == 0
    for fmt in ("csv", "xlsx", "json"):
        if kind != "viewer":
            s.dataset_records.export_dataset(
                u.id, d.key, fmt, prefer_concept=kind in {"owner", "admin"}
            ).content.close()
        else:
            with pytest.raises(NotFoundError):
                s.dataset_records.export_dataset(u.id, d.key, fmt)
    if kind in {"owner", "admin"}:
        s.datasets.update_dataset(u.id, d.key, "Changed", "")
        s.dataset_fields.create_field(u.id, d.key, "code", "Code", "string", True, True, True)
        row = s.dataset_records.create_record(u.id, d.key, {"code": "NL"})
        s.dataset_records.update_record(u.id, d.key, row.id, 1, {"code": "BE"})
        s.dataset_records.delete_record(u.id, d.key, row.id, 2)
    else:
        with pytest.raises(NotFoundError):
            s.datasets.update_dataset(u.id, d.key, "Changed", "")
        with pytest.raises(NotFoundError):
            s.dataset_fields.create_field(u.id, d.key, "code", "Code", "string")
        with pytest.raises(NotFoundError):
            s.datasets.change_owner(u.id, d.key, owner.id, other.id)
        with pytest.raises(NotFoundError):
            s.datasets.grant_export_access(u.id, d.key, "user", u.id)
    with factory() as session:
        assert session.scalar(select(func.count(Grant.id))) == 0


@pytest.mark.parametrize("kind", ["missing", "inactive", "deleted"])
def test_ineligible_users_denied_every_capability(governance, kind):
    _, _, s, a, u, owner, _, d = governance
    s.memberships.create_membership(a.id, u.id, owner.id)
    s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    if kind == "inactive":
        s.users.disable_user(a.id, u.id)
    elif kind == "deleted":
        s.users.delete_user(a.id, u.id)
    actor = "missing" if kind == "missing" else u.id
    auth = s.authorization.datasets
    for name in (
        "can_view_dataset",
        "can_maintain_dataset",
        "can_export_dataset",
        "can_change_dataset_owner",
        "can_manage_export_access",
    ):
        assert not getattr(auth, name)(actor, d.id)
    assert not auth.can_create_dataset(actor, owner.id)
    with pytest.raises(AuthorizationError):
        s.datasets.create_dataset(actor, "denied", "Denied", "", owner.id)
    with pytest.raises(AuthorizationError):
        s.datasets.list_datasets(actor)
    with pytest.raises(NotFoundError):
        s.datasets.get_dataset(actor, d.key)


def test_creation_zero_one_multiple_teams_and_forged_selection(governance):
    _, factory, s, a, u, owner, other, _ = governance
    with pytest.raises(ValidationError):
        s.datasets.create_dataset(u.id, "none", "None", "")
    first = s.memberships.create_membership(a.id, u.id, owner.id)
    d = s.datasets.create_dataset(u.id, "single", "Single", "")
    assert d.owner_team_id == owner.id and d.owner_user_id == u.id
    with pytest.raises(ValidationError):
        s.datasets.create_dataset(u.id, "forged", "Forged", "", other.id)
    s.memberships.create_membership(a.id, u.id, other.id)
    with pytest.raises(ValidationError):
        s.datasets.create_dataset(u.id, "ambiguous", "Ambiguous", "")
    assert (
        s.datasets.create_dataset(u.id, "chosen", "Chosen", "", other.id).owner_team_id == other.id
    )
    s.memberships.delete_membership(a.id, owner.id, first.id)
    with pytest.raises(ValidationError):
        s.datasets.create_dataset(u.id, "stale", "Stale", "", owner.id)
    with factory() as session:
        audit = session.scalar(select(AuditEvent).where(AuditEvent.object_id == d.id))
        assert audit.details == {"owner_team_id": owner.id, "creator_user_id": u.id}
        assert session.scalar(select(func.count(Grant.id))) == 0


def test_current_membership_and_export_are_additive(governance):
    _, _, s, a, u, owner, other, d = governance
    auth = s.authorization.datasets
    own = s.memberships.create_membership(a.id, u.id, owner.id)
    assert auth.can_maintain_dataset(u.id, d.id)
    s.memberships.delete_membership(a.id, owner.id, own.id)
    assert not auth.can_maintain_dataset(u.id, d.id)
    assert not auth.can_export_dataset(u.id, d.id)
    direct = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    team = s.datasets.grant_export_access(a.id, d.key, "team", other.id)
    membership = s.memberships.create_membership(a.id, u.id, other.id)
    s.datasets.revoke_export_access(a.id, d.key, direct.id)
    assert auth.can_export_dataset(u.id, d.id)
    s.memberships.delete_membership(a.id, other.id, membership.id)
    assert not auth.can_export_dataset(u.id, d.id)
    s.datasets.revoke_export_access(a.id, d.key, team.id)
    assert not auth.can_maintain_dataset(u.id, d.id)


@pytest.mark.parametrize("permission", ["viewer", "editor", "admin"])
@pytest.mark.parametrize("subject_type", ["user", "team"])
def test_legacy_grants_and_creator_supply_no_maintenance_or_export(
    governance, permission, subject_type
):
    _, factory, s, a, u, owner, other, d = governance
    with factory.begin() as session:
        session.get(Dataset, d.id).owner_user_id = u.id
    if subject_type == "team":
        s.memberships.create_membership(a.id, u.id, other.id)
    subject_id = u.id if subject_type == "user" else other.id
    s.grants.create_grant(a.id, subject_type, subject_id, "dataset", d.id, permission)
    assert s.authorization.effective_permission(u.id, "dataset", d.id) is None
    assert not s.authorization.check_access(u.id, "dataset", d.id, "editor")
    assert not s.authorization.datasets.can_export_dataset(u.id, d.id)
    s.users.delete_user(a.id, u.id)


def test_ownership_cas_stale_noop_and_immediate_transfer(governance):
    _, factory, s, a, u, owner, other, d = governance
    s.memberships.create_membership(a.id, u.id, owner.id)
    access = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    original = state(factory, d)
    s.datasets.change_owner(a.id, d.key, owner.id, owner.id)
    assert state(factory, d) == original
    reassigned = s.datasets.change_owner(a.id, d.key, owner.id, other.id)
    assert reassigned.owner_team_name == other.name
    changed = state(factory, d)
    assert changed[0] == other.id and changed[1] != original[1] and changed[2] == original[2] + 1
    assert not s.authorization.datasets.can_maintain_dataset(u.id, d.id)
    assert s.authorization.datasets.can_export_dataset(u.id, d.id)
    for target in (owner.id, other.id):
        with pytest.raises(StaleDatasetOwnerError):
            s.datasets.change_owner(a.id, d.key, owner.id, target)
        assert state(factory, d) == changed
    with factory() as session:
        assert session.get(DatasetExportAccess, access.id)
        event = session.scalar(select(AuditEvent).where(AuditEvent.action == "owner_changed"))
        assert event.details == {"previous_owner_team_id": owner.id, "owner_team_id": other.id}


@pytest.mark.parametrize("expected", [None, "", "bad", "00000000-0000-0000-0000-000000000000"])
def test_missing_or_tampered_expected_owner_never_writes(governance, expected):
    _, factory, s, a, _, _, other, d = governance
    original = state(factory, d)
    with pytest.raises((ValidationError, StaleDatasetOwnerError)):
        s.datasets.change_owner(a.id, d.key, expected, other.id)
    assert state(factory, d) == original


def test_team_lifecycle_and_deleted_target(governance):
    _, factory, s, a, u, owner, other, d = governance
    with pytest.raises(InvalidLifecycleError):
        s.teams.delete_team(a.id, owner.id)
    with pytest.raises(AuthorizationError):
        s.teams.delete_team(u.id, owner.id)
    s.teams.delete_team(a.id, other.id)
    before = state(factory, d)
    with pytest.raises(ValidationError):
        s.datasets.change_owner(a.id, d.key, owner.id, other.id)
    assert state(factory, d) == before


@pytest.mark.parametrize("failure", ["audit", "persistence"])
def test_owner_failure_rolls_back_cas(database_governance, monkeypatch, failure):
    _, factory, s, a, _, owner, other, d = database_governance
    before = state(factory, d)
    if failure == "audit":

        def fail(*args, **kwargs):
            raise RuntimeError("audit failed")

        monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    else:
        original = SqlAlchemyDatasetOperations.change_owner_if_expected

        def fail(self, *args):
            original(self, *args)
            raise RuntimeError("persistence failed")

        monkeypatch.setattr(SqlAlchemyDatasetOperations, "change_owner_if_expected", fail)
    with pytest.raises(RuntimeError):
        s.datasets.change_owner(a.id, d.key, owner.id, other.id)
    assert state(factory, d) == before


def test_two_administrators_have_exactly_one_successful_cas(governance):
    _, factory, s, a, _, owner, other, d = governance
    second = s.users.create_user(
        a.id, "second@example.com", "Second", "valid password", role="admin"
    )
    third = s.teams.create_team(a.id, "Third")
    barrier = Barrier(2)

    def change(actor, target):
        barrier.wait(timeout=10)
        try:
            s.datasets.change_owner(actor, d.key, owner.id, target)
            return "changed"
        except StaleDatasetOwnerError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [
            executor.submit(change, a.id, other.id),
            executor.submit(change, second.id, third.id),
        ]
        assert sorted(task.result(timeout=20) for task in tasks) == ["changed", "stale"]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "owner_changed")
            )
            == 1
        )


def test_target_deleted_while_reassignment_starts(governance):
    _, factory, s, a, _, owner, other, d = governance
    before = state(factory, d)
    started = Event()

    def reassign():
        started.set()
        return s.datasets.change_owner(a.id, d.key, owner.id, other.id)

    with ThreadPoolExecutor(max_workers=1) as executor:
        # Hold the deletion transaction while a separate connection starts the
        # reassignment. SQLite waits at BEGIN IMMEDIATE; PostgreSQL at the team lock.
        with s.datasets.uow(write=True, serialized=True) as work:
            target = work.teams.get(other.id, for_update=True)
            work.teams.delete(target)
            work.flush()
            task = executor.submit(reassign)
            assert started.wait(timeout=10)
        with pytest.raises(ValidationError):
            task.result(timeout=20)
    assert state(factory, d) == before


def test_import_checks_current_membership_at_confirmation(governance):
    _, factory, s, a, u, owner, _, d = governance
    membership = s.memberships.create_membership(a.id, u.id, owner.id)
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", is_required=True)
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    importer = s.record_imports
    first, _, _ = importer.begin(u.id, "member-session", d.key, "one.csv", b"code,extra\nA,x\n")
    assert importer.preview(u.id, "member-session", d.key, first.token, ("code", None)).valid
    assert importer.confirm(u.id, "member-session", d.key, first.token) == 1
    second, _, _ = importer.begin(u.id, "member-session", d.key, "two.csv", b"code,extra\nB,x\n")
    assert importer.preview(u.id, "member-session", d.key, second.token, ("code", None)).valid
    s.memberships.delete_membership(a.id, owner.id, membership.id)
    before = state(factory, d)
    with pytest.raises(NotFoundError):
        importer.confirm(u.id, "member-session", d.key, second.token)
    assert state(factory, d) == before
    with factory() as session:
        assert session.scalar(select(func.count(DatasetRecord.id))) == 1


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_export_audit_failure_rolls_back(database_governance, monkeypatch, operation):
    _, factory, s, a, u, _, _, d = database_governance
    access = None
    if operation == "revoke":
        access = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    before = state(factory, d)

    def fail(*args, **kwargs):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    with pytest.raises(RuntimeError):
        if operation == "grant":
            s.datasets.grant_export_access(a.id, d.key, "user", u.id)
        else:
            s.datasets.revoke_export_access(a.id, d.key, access.id)
    assert state(factory, d) == before
    assert s.authorization.datasets.can_export_dataset(u.id, d.id) == (operation == "revoke")


def test_concurrent_export_grants_are_unique(database_governance):
    _, factory, s, a, u, _, _, d = database_governance
    barrier = Barrier(2)

    def grant():
        barrier.wait(timeout=10)
        try:
            s.datasets.grant_export_access(a.id, d.key, "user", u.id)
            return "granted"
        except ConflictError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [executor.submit(grant) for _ in range(2)]
        assert sorted(task.result(timeout=20) for task in tasks) == ["duplicate", "granted"]
    with factory() as session:
        assert session.scalar(select(func.count(DatasetExportAccess.id))) == 1
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_type == "dataset_export_access"
                )
            )
            == 1
        )


def test_export_duplicates_constraints_and_cross_dataset_revoke(database_governance):
    _, factory, s, a, u, owner, _, d = database_governance
    access = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    with pytest.raises(ConflictError):
        s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(DatasetExportAccess(dataset_id=d.id, user_id=u.id))
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(DatasetExportAccess(dataset_id=d.id, user_id=u.id, team_id=owner.id))
    second = s.datasets.create_dataset(a.id, "second", "Second", "", owner.id)
    with pytest.raises(NotFoundError):
        s.datasets.revoke_export_access(a.id, second.key, access.id)
    s.users.disable_user(a.id, u.id)
    with pytest.raises(ValidationError):
        s.datasets.grant_export_access(a.id, second.key, "user", u.id)


def test_browser_creation_read_export_owner_csrf_and_escaping(governance):
    _, factory, s, a, u, owner, other, d = governance
    publish_dataset(s, a.id, d)
    app = create_flask_app(
        s.users,
        s.authentication,
        s.sessions,
        s.teams,
        s.memberships,
        authorization=s.authorization,
        datasets=s.datasets,
        dataset_fields=s.dataset_fields,
        dataset_business_keys=s.dataset_business_keys,
        dataset_records=s.dataset_records,
    )
    client = app.test_client()
    from refrncr.web import SESSION_COOKIE

    def sign_in(actor):
        token, login = s.sessions.create(actor)
        client.set_cookie(SESSION_COOKIE, token, domain="localhost")
        return login.csrf_token

    def get(path):
        return client.get(path, base_url="https://localhost")

    def post(path, data):
        return client.post(path, data=data, base_url="https://localhost")

    csrf = sign_in(u)
    assert get("/datasets/new").status_code == 200
    assert b"Team membership is required" in get("/datasets/new").data
    assert get("/teams").status_code == 200
    for path in ("/datasets", f"/datasets/{d.key}", f"/datasets/{d.key}/records"):
        assert get(path).status_code == 200
    assert b"export-menu" not in get(f"/datasets/{d.key}/records").data
    for fmt in ("csv", "xlsx", "json"):
        assert get(f"/datasets/{d.key}/records/export?format={fmt}").status_code == 404
    s.memberships.create_membership(a.id, u.id, owner.id)
    assert (
        post(
            "/datasets",
            {
                "key": "created",
                "name": "Created",
                "description": "",
                "owner_team_id": owner.id,
                "csrf_token": csrf,
            },
        ).status_code
        == 302
    )
    assert get(f"/datasets/{d.key}/owner").status_code == 404
    csrf = sign_in(a)
    detail = get(f"/datasets/{d.key}").text
    assert "id='access-management-heading'>Access</h2>" in detail
    assert (
        f"<a class='access-owner-action' href='/datasets/{d.key}?edit=1#owner-team'>"
        "Change owning team</a>" in detail
    )
    assert "All active Community users can view this dataset." not in detail
    s.dataset_versions.create_concept(a.id, d.id)
    edit = get(f"/datasets/{d.key}/edit").text
    assert "name='expected_owner_team_id'" in edit
    before = state(factory, d)
    assert (
        post(
            f"/datasets/{d.key}",
            {
                "name": "Rejected",
                "description": "",
                "csrf_token": csrf,
                "owner_team_id": other.id,
            },
        ).status_code
        == 400
    )
    assert state(factory, d) == before
    # The separate owner page is gone; the owning team changes through the edit form.
    assert get(f"/datasets/{d.key}/owner").status_code == 404
    owner_change = {"owner_team_id": other.id, "expected_owner_team_id": owner.id}
    assert post(f"/datasets/{d.key}/owner", owner_change | {"csrf_token": csrf}).status_code == 404
    s.datasets.change_owner(a.id, d.key, owner.id, other.id)
    s.teams.rename_team(a.id, other.id, '<img src=x onerror="alert(1)">')
    assert b"&lt;img" in get(f"/datasets/{d.key}").data
    assert b"<img src=x" not in get(f"/datasets/{d.key}").data
    concept_url = f"/datasets/{d.key}/concept"
    access_url = f"/datasets/{d.key}/export-access"
    assert get(access_url).status_code == 405

    def access_section(html):
        return html.split("<section id='access-management'", 1)[1].split(
            "<section id='danger-zone'", 1
        )[0]

    empty = access_section(get(concept_url).text)
    for copy in (
        "Who has access",
        "Owning team",
        "Dataset access",
        "User or team",
        "Select a user or team",
        "Grant access",
        "Export only: they can view and download the records, not change them.",
        "Only the owning team has access. Add a user or team below to give them export access.",
    ):
        assert copy in empty
    assert "&lt;img src=x" in empty
    for right in ("View", "Edit", "Publish", "Export"):
        assert f"<span class='badge badge-subtle'>{right}</span>" in empty
    assert "<img src=x" not in empty
    assert empty.count("<table") == 1
    assert "class='access-owner-row'" in empty
    assert "data-access-entry" not in empty
    assert "data-requires-selection" in empty and "data-inline-refresh" in empty
    assert "data-panel-refresh" not in empty
    assert f"action='/datasets/{d.key}/export-access?concept=1'" in empty
    invalid = post(f"{access_url}?concept=1", {"subject": "", "csrf_token": csrf})
    assert invalid.status_code == 400
    assert "id='access-management-heading'" in invalid.text
    assert "aria-describedby='export-subject-help export-subject-error' aria-invalid='true'" in (
        invalid.text
    )
    assert "data-error-focus" in invalid.text
    assert post(access_url, {"subject": f"user:{u.id}"}).status_code == 403
    granted = post(f"{access_url}?concept=1", {"subject": f"user:{u.id}", "csrf_token": csrf})
    assert granted.status_code == 302
    assert granted.location == f"/datasets/{d.key}/concept#access-management"
    duplicate = post(f"{access_url}?concept=1", {"subject": f"user:{u.id}", "csrf_token": csrf})
    assert duplicate.status_code == 409
    assert f"value='user:{u.id}' selected" in duplicate.text
    team = post(f"{access_url}?standalone=1", {"subject": f"team:{owner.id}", "csrf_token": csrf})
    assert team.status_code == 302
    assert team.location == f"/datasets/{d.key}/configure#access-management"
    populated = access_section(get(concept_url).text)
    assert populated.count("<table") == 1
    assert populated.index("class='access-owner-row'") < populated.index("data-access-entry")
    assert sum("data-access-entry" in attrs for tag, attrs in html_elements(populated)) == 2
    assert "<span class='badge'>User</span>" in populated
    assert "<span class='badge'>Team</span>" in populated
    assert "<span class='badge badge-identifier'>Owning team</span>" in populated
    assert f"<span class='access-subject-detail'>{u.email}</span>" in populated
    assert "Remove export access for User" in populated
    assert "Remove export access for Team Data domain" in populated
    assert "data-confirm='Remove export access for User?'" in populated
    assert "data-confirm='Remove export access for Team Data domain?'" in populated
    assert "button-danger" not in populated
    assert f"Remove export access for {a.id}" not in populated
    with factory() as session:
        access = session.scalar(
            select(DatasetExportAccess).where(DatasetExportAccess.team_id == owner.id)
        )
    revoke = f"{access_url}/{access.id}/revoke"
    assert post(revoke, {}).status_code == 403
    revoked = post(f"{revoke}?concept=1", {"csrf_token": csrf})
    assert revoked.status_code == 302
    assert revoked.location == f"/datasets/{d.key}/concept#access-management"
    with factory() as session:
        assert session.get(DatasetExportAccess, access.id) is None
        assert {
            event.action
            for event in session.scalars(
                select(AuditEvent).where(AuditEvent.object_id == access.id)
            )
        } == {"granted", "revoked"}
    csrf = sign_in(u)
    assert post(access_url, {"subject": f"team:{owner.id}", "csrf_token": csrf}).status_code == 404
    for fmt in ("csv", "xlsx", "json"):
        response = get(f"/datasets/{d.key}/records/export?format={fmt}")
        assert response.status_code == 200
        response.close()


def test_combined_edit_is_atomic_and_rejects_stale_ownership(database_governance, monkeypatch):
    _, factory, s, a, u, owner, other, d = database_governance
    before = state(factory, d)
    s.datasets.update_dataset(a.id, d.key, d.name, d.description, owner_change=(owner.id, owner.id))
    assert state(factory, d) == before
    original_audit = SqlAlchemyUnitOfWork.add_audit

    def fail_metadata(self, **values):
        if values["action"] == "metadata_updated":
            raise RuntimeError("metadata audit failed")
        return original_audit(self, **values)

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_metadata)
    with pytest.raises(RuntimeError):
        s.datasets.update_dataset(
            a.id, d.key, "Changed", "Description", owner_change=(owner.id, other.id)
        )
    assert state(factory, d) == before
    assert s.datasets.get_dataset(a.id, d.key).name == d.name
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", original_audit)
    s.datasets.update_dataset(
        a.id, d.key, "Changed", "Description", owner_change=(owner.id, other.id)
    )
    before = state(factory, d)
    with pytest.raises(StaleDatasetOwnerError):
        s.datasets.update_dataset(
            a.id, d.key, "Stale", "Rejected", owner_change=(owner.id, other.id)
        )
    assert state(factory, d) == before
    assert s.datasets.get_dataset(a.id, d.key).name == "Changed"
    s.memberships.create_membership(a.id, u.id, other.id)
    before = state(factory, d)
    with pytest.raises(NotFoundError):
        s.datasets.update_dataset(u.id, d.key, "Forged", "", owner_change=(other.id, owner.id))
    assert state(factory, d) == before
