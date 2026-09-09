"""REF-73 review: concrete service boundaries, rollback and concurrent revocation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_dataset_governance import governance, state  # noqa: F401

from refrncr import cli
from refrncr.models import Dataset, DatasetExportAccess
from refrncr.persistence import SqlAlchemyDatasetOperations, SqlAlchemyUnitOfWork
from refrncr.services import AuthorizationError, NotFoundError


@pytest.mark.parametrize(
    "kind", ["ordinary", "export", "owner", "admin", "inactive", "deleted", "anonymous"]
)
def test_each_service_boundary(governance, kind):  # noqa: F811
    _, factory, s, a, u, owner, other, d = governance
    code = s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, True, True)
    spare = s.dataset_fields.create_field(a.id, d.key, "spare", "Spare", "string")
    row = s.dataset_records.create_record(a.id, d.key, {"code": "NL"})
    entry = s.datasets.grant_export_access(a.id, d.key, "team", other.id)
    if kind in {"owner", "inactive", "deleted"}:
        s.memberships.create_membership(a.id, u.id, owner.id)
    if kind in {"export", "inactive", "deleted"}:
        s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    if kind == "inactive":
        s.users.disable_user(a.id, u.id)
    if kind == "deleted":
        s.users.delete_user(a.id, u.id)
    actor = a.id if kind == "admin" else None if kind == "anonymous" else u.id
    active = kind not in {"inactive", "deleted", "anonymous"}
    maintainer = kind in {"owner", "admin"}

    def check(operation, allowed, error=NotFoundError):
        if allowed:
            return operation()
        before = state(factory, d)
        with pytest.raises(error):
            operation()
        assert state(factory, d) == before

    check(lambda: s.datasets.list_dataset_overview(actor), active, AuthorizationError)
    check(lambda: s.datasets.get_dataset_details(actor, d.key), active)
    check(lambda: s.dataset_fields.get_field(actor, d.key, "code"), active)
    check(lambda: s.dataset_business_keys.get_business_key(actor, d.key), active)
    check(lambda: s.dataset_records.list_records(actor, d.key), active)
    for fmt in ("csv", "xlsx", "json"):
        result = check(
            lambda fmt=fmt: s.dataset_records.export_dataset(actor, d.key, fmt),
            maintainer or kind == "export",
        )
        if result:
            result.content.close()
    check(lambda: s.datasets.update_dataset(actor, d.key, "Updated", ""), maintainer)
    check(
        lambda: s.dataset_fields.update_field(
            actor, d.key, "spare", "Renamed", "string", False, False
        ),
        maintainer,
    )
    check(
        lambda: s.dataset_fields.replace_order(
            actor, d.key, (code.id, spare.id), (spare.id, code.id)
        ),
        maintainer,
    )
    check(lambda: s.dataset_business_keys.set_business_key(actor, d.key, ("code",)), maintainer)
    check(
        lambda: s.dataset_records.update_record(actor, d.key, row.id, 1, {"code": "BE"}), maintainer
    )
    check(lambda: s.dataset_records.delete_record(actor, d.key, row.id, 2), maintainer)
    check(lambda: s.dataset_records.create_record(actor, d.key, {"code": "DE"}), maintainer)
    check(lambda: s.dataset_fields.delete_field(actor, d.key, "spare"), maintainer)
    check(lambda: s.dataset_fields.create_field(actor, d.key, "new", "New", "string"), maintainer)
    staged = check(
        lambda: s.record_imports.begin(actor, "review", d.key, "rows.csv", b"code,extra\nFR,x\n"),
        maintainer,
    )
    if staged:
        token = staged[0].token
        assert s.record_imports.preview(actor, "review", d.key, token, ("code", None)).valid
        assert s.record_imports.confirm(actor, "review", d.key, token) == 1
    check(lambda: s.datasets.export_access_context(actor, d.key), kind == "admin")
    check(lambda: s.datasets.grant_export_access(actor, d.key, "user", a.id), kind == "admin")
    check(lambda: s.datasets.revoke_export_access(actor, d.key, entry.id), kind == "admin")
    check(lambda: s.datasets.change_owner(actor, d.key, owner.id, other.id), kind == "admin")
    if kind != "admin":
        check(lambda: s.teams.create_team(actor, "Escalated"), False, AuthorizationError)
        check(
            lambda: s.users.create_user(
                actor, "escalated@example.com", "Escalated", "valid password"
            ),
            False,
            AuthorizationError,
        )
        check(
            lambda: s.memberships.create_membership(actor, u.id, other.id),
            False,
            AuthorizationError,
        )


@pytest.mark.parametrize("operation", ["create", "owner", "grant", "revoke"])
@pytest.mark.parametrize("position", ["audit", "commit"])
def test_all_governance_writes_rollback_after_flush(governance, monkeypatch, operation, position):  # noqa: F811
    _, factory, s, a, u, owner, other, d = governance
    access = (
        s.datasets.grant_export_access(a.id, d.key, "user", u.id) if operation == "revoke" else None
    )

    def snapshot():
        with factory() as session:
            return (
                state(factory, d),
                session.scalar(select(func.count(Dataset.id))),
                session.scalar(select(func.count(DatasetExportAccess.id))),
            )

    before = snapshot()
    original = SqlAlchemyUnitOfWork.add_audit

    def fail_audit(self, **values):
        original(self, **values)
        self.flush()
        raise RuntimeError("injected after audit persistence")

    def fail_commit(self):
        self.flush()
        raise RuntimeError("injected before commit")

    with monkeypatch.context() as patch:
        if position == "audit":
            patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
        else:
            patch.setattr(Session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected"):
            if operation == "create":
                s.datasets.create_dataset(a.id, "rollback", "Rollback", "", owner.id)
            elif operation == "owner":
                s.datasets.change_owner(a.id, d.key, owner.id, other.id)
            elif operation == "grant":
                s.datasets.grant_export_access(a.id, d.key, "user", u.id)
            else:
                s.datasets.revoke_export_access(a.id, d.key, access.id)
    assert snapshot() == before


def test_export_snapshot_survives_concurrent_revoke(governance, monkeypatch):  # noqa: F811
    engine, _, s, a, u, _, _, d = governance
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    access = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    opened, resume = Event(), Event()
    original = SqlAlchemyDatasetOperations.find_by_key

    def pause(self, key, *, for_update=False):
        result = original(self, key, for_update=for_update)
        if not for_update and not opened.is_set():
            opened.set()
            assert resume.wait(10)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyDatasetOperations, "find_by_key", pause)
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(s.dataset_records.export_dataset, u.id, d.key, "json")
            try:
                assert opened.wait(10)
                s.datasets.revoke_export_access(a.id, d.key, access.id)
            finally:
                resume.set()
            exported = task.result(timeout=10)
            exported.content.close()
    with pytest.raises(NotFoundError):
        s.dataset_records.export_dataset(u.id, d.key, "json")


@pytest.mark.parametrize("operation", ["create", "maintain"])
def test_membership_removal_commits_before_waiting_mutation(governance, operation):  # noqa: F811
    _, factory, s, a, u, owner, _, d = governance
    membership = s.memberships.create_membership(a.id, u.id, owner.id)
    started = Event()

    def mutate():
        started.set()
        if operation == "create":
            return s.datasets.create_dataset(u.id, "removed", "Removed", "", owner.id)
        return s.datasets.update_dataset(u.id, d.key, "Rejected", "")

    before = state(factory, d)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with s.datasets.uow(write=True, serialized=True) as work:
            work.teams.get(owner.id, for_update=True)
            work.memberships.delete(work.memberships.get(membership.id))
            work.flush()
            task = pool.submit(mutate)
            assert started.wait(10)
        from refrncr.services import ValidationError

        with pytest.raises(ValidationError if operation == "create" else NotFoundError):
            task.result(timeout=10)
    assert state(factory, d) == before


def test_maintenance_listing_query_count_is_bounded(governance):  # noqa: F811
    engine, _, s, a, u, owner, _, _ = governance
    s.memberships.create_membership(a.id, u.id, owner.id)
    statements = []

    def record(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", record)
    try:
        first = s.authorization.authorized_resources(u.id, "dataset", "editor")
        baseline = len(statements)
        for i in range(5):
            s.datasets.create_dataset(a.id, f"more-{i}", "More", "", owner.id)
        statements.clear()
        second = s.authorization.authorized_resources(u.id, "dataset", "editor")
        assert len(statements) == baseline
        assert len(second.resource_ids) == len(first.resource_ids) + 5
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_preflight_does_not_print_database_error_details(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("password=secret SELECT sensitive FROM internal_table")

    monkeypatch.setattr(cli, "create_database_engine", fail)
    assert cli.main(["dataset-ownership-preflight"]) == 1
    output = capsys.readouterr().err
    assert "keep writers stopped" in output
    assert "secret" not in output and "SELECT" not in output


def test_admin_creation_zero_one_and_multiple_teams(governance):  # noqa: F811
    _, factory, s, a, _, owner, other, d = governance
    from refrncr.services import ValidationError

    with factory.begin() as session:
        session.delete(session.get(Dataset, d.id))
    s.teams.delete_team(a.id, owner.id)
    s.teams.delete_team(a.id, other.id)
    with pytest.raises(ValidationError):
        s.datasets.create_dataset(a.id, "zero", "Zero", "")
    only = s.teams.create_team(a.id, "Only")
    assert s.datasets.create_dataset(a.id, "one", "One", "").owner_team_id == only.id
    extra = s.teams.create_team(a.id, "Extra")
    with pytest.raises(ValidationError):
        s.datasets.create_dataset(a.id, "ambiguous", "Ambiguous", "")
    assert (
        s.datasets.create_dataset(a.id, "chosen", "Chosen", "", extra.id).owner_team_id == extra.id
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_user",
        "missing_team",
        "missing_dataset",
        "no_subject",
        "both_subjects",
        "duplicate_team",
        "unowned_dataset",
    ],
)
def test_database_relationships_fail_closed(governance, invalid):  # noqa: F811
    import uuid

    from sqlalchemy.exc import IntegrityError

    _, factory, s, a, u, owner, other, d = governance
    missing = str(uuid.uuid4())
    values = {"dataset_id": d.id, "user_id": u.id}
    if invalid == "missing_user":
        values["user_id"] = missing
    elif invalid == "missing_team":
        values = {"dataset_id": d.id, "team_id": missing}
    elif invalid == "missing_dataset":
        values["dataset_id"] = missing
    elif invalid == "no_subject":
        values = {"dataset_id": d.id}
    elif invalid == "both_subjects":
        values["team_id"] = owner.id
    elif invalid == "duplicate_team":
        s.datasets.grant_export_access(a.id, d.key, "team", other.id)
        values = {"dataset_id": d.id, "team_id": other.id}
    with pytest.raises(IntegrityError), factory.begin() as session:
        if invalid == "unowned_dataset":
            session.get(Dataset, d.id).owner_team_id = None
        else:
            session.add(DatasetExportAccess(**values))


def test_export_team_deletion_cascades_without_removing_other_access(governance):  # noqa: F811
    _, factory, s, a, u, _, other, d = governance
    s.memberships.create_membership(a.id, u.id, other.id)
    team = s.datasets.grant_export_access(a.id, d.key, "team", other.id)
    direct = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    s.teams.delete_team(a.id, other.id)
    with factory() as session:
        assert session.get(DatasetExportAccess, team.id) is None
        assert session.get(DatasetExportAccess, direct.id) is not None
    assert s.authorization.datasets.can_export_dataset(u.id, d.id)
    assert not s.authorization.datasets.can_maintain_dataset(u.id, d.id)


def test_concurrent_revoke_has_one_audit_and_safe_missing_result(governance):  # noqa: F811
    from threading import Barrier

    from refrncr.models import AuditEvent

    _, factory, s, a, u, _, _, d = governance
    access = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
    barrier = Barrier(2)

    def revoke():
        barrier.wait(timeout=10)
        try:
            s.datasets.revoke_export_access(a.id, d.key, access.id)
            return "revoked"
        except NotFoundError:
            return "missing"

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(revoke) for _ in range(2)]
        assert sorted(task.result(timeout=15) for task in tasks) == ["missing", "revoked"]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == access.id, AuditEvent.action == "revoked"
                )
            )
            == 1
        )
    assert not s.authorization.datasets.can_export_dataset(u.id, d.id)
