"""REF-39 read, privacy and pagination contracts on both supported databases."""

import ast
import html as html_module
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from test_audit_coverage import audit_db as audit_db

from refrncr.audit import EVENTS, AuditContractError
from refrncr.audit_history import (
    CURSOR_LIFETIME,
    AuditHistoryService,
    parse_audit_filters,
    parse_audit_time,
)
from refrncr.audit_presentation import (
    ACTION_LABELS,
    OBJECTS,
    SYSTEM_ACTORS,
    require_complete_presentation,
    safe_context,
    safe_name,
)
from refrncr.db.base import Base
from refrncr.models import AuditEvent, Dataset, DatasetField, Team, User
from refrncr.persistence import SqlAlchemyAuditReadOperations
from refrncr.ports import AuditFilters
from refrncr.services import AuthorizationError, NotFoundError, ValidationError
from refrncr.web import SESSION_COOKIE, create_flask_app

INSTANT = datetime(2026, 3, 29, 0, 59, 59, 123456, tzinfo=UTC)


@pytest.fixture
def history(audit_db):
    engine, sessions, services = audit_db
    admin, reader = str(uuid4()), str(uuid4())
    with sessions.begin() as session:
        for uid, role in ((admin, "admin"), (reader, "user")):
            session.add(
                User(
                    id=uid,
                    email=f"{uid}@example.com",
                    display_name=role.title(),
                    password_hash="test-only-unused",
                    role=role,
                )
            )
    return SimpleNamespace(
        engine=engine,
        sessions=sessions,
        s=services,
        admin=admin,
        reader=reader,
        service=services.audit_history,
    )


def seed(
    h,
    count,
    *,
    first=1,
    timestamp=INSTANT,
    actor=None,
    kind="user",
    action="created",
    target=None,
    details=None,
):
    with h.sessions.begin() as session:
        session.add_all(
            [
                AuditEvent(
                    id=str(UUID(int=i)),
                    timestamp=timestamp,
                    actor_user_id=actor or h.admin,
                    object_type=kind,
                    object_id=target or h.reader,
                    action=action,
                    details=details,
                )
                for i in range(first, first + count)
            ]
        )
    return [str(UUID(int=i)) for i in reversed(range(first, first + count))]


def state(h):
    with h.sessions() as session:
        return {
            t.name: sorted(repr(tuple(r)) for r in session.execute(select(t)))
            for t in Base.metadata.sorted_tables
        }


def client(h, uid):
    app = create_flask_app(
        h.s.users,
        h.s.authentication,
        h.s.sessions,
        h.s.teams,
        h.s.memberships,
        audit_history=h.service,
    )
    app.testing = True
    result = app.test_client()
    if uid:
        with h.sessions() as session:
            user = session.get(User, uid)
        token, _ = h.s.sessions.create(user)
        result.set_cookie(SESSION_COOKIE, token, domain="localhost")
    return result


@pytest.mark.parametrize("count", [0, 1, 49, 50, 51, 100, 101, 127, 10000])
def test_complete_history_and_cursor_round_trip(history, count):
    h = history
    expected = seed(h, count)
    before = state(h)
    page = h.service.list_history(h.admin)
    assert len(page.items) == min(50, count)
    assert page.previous_cursor is None
    seen = [item.id for item in page.items]
    previous = page
    while page.next_cursor:
        page = h.service.list_history(h.admin, cursor=page.next_cursor)
        assert 1 <= len(page.items) <= 50
        back = h.service.list_history(h.admin, cursor=page.previous_cursor)
        assert back.items == previous.items
        seen.extend(item.id for item in page.items)
        previous = page
    assert seen == expected
    assert len(set(seen)) == count
    assert state(h) == before


def test_insert_during_pagination_does_not_skip_or_duplicate(history):
    h = history
    expected = seed(h, 125)
    first = h.service.list_history(h.admin)
    seed(h, 10, first=1000, timestamp=INSTANT + timedelta(seconds=1))
    seed(h, 10, first=2000)  # Same timestamp, higher tie-breaker.
    second = h.service.list_history(h.admin, cursor=first.next_cursor)
    third = h.service.list_history(h.admin, cursor=second.next_cursor)
    assert [i.id for p in (first, second, third) for i in p.items] == expected
    assert h.service.list_history(h.admin, cursor=second.previous_cursor).items == first.items
    assert h.service.list_history(h.admin).items[0].id == str(UUID(int=1009))


def test_filters_and_inclusive_exclusive_boundaries(history):
    h = history
    seed(h, 1, timestamp=INSTANT - timedelta(microseconds=1))
    wanted = seed(h, 1, first=2, actor=h.reader, kind="team", action="renamed")
    seed(h, 1, first=3, timestamp=INSTANT + timedelta(microseconds=1))
    filters = AuditFilters(
        start=INSTANT,
        end=INSTANT + timedelta(microseconds=1),
        actor=h.reader,
        action="team:renamed",
        object_type="team",
    )
    for key in ("start", "end", "actor", "action", "object_type"):
        result = h.service.list_history(h.admin, AuditFilters(**{key: getattr(filters, key)}))
        assert wanted[0] in [i.id for i in result.items]
        assert len(result.items) == (2 if key in {"start", "end"} else 1)
    assert [i.id for i in h.service.list_history(h.admin, filters).items] == wanted
    assert not h.service.list_history(h.admin, replace(filters, actor=str(uuid4()))).items
    assert not h.service.list_history(h.admin, replace(filters, end=INSTANT)).items
    assert parse_audit_time("2026-03-29T01:59:59.123456+01:00") == INSTANT


@pytest.mark.parametrize(
    "pairs",
    [
        [("start", "2026-03-29T03:00")],
        [("start", "not a date")],
        [("start", "2026-03-29T03:00+00:99")],
        [("start", "2026-02-30T03:00Z")],
        [("start", "2026-03-30T00:00Z"), ("end", "2026-03-29T00:00Z")],
        [("action", "created")],
        [("object_type", "secret")],
        [("actor", "1")],
        [("actor", ""), ("actor", "")],
        [("search", "secret")],
        [("cursor", "")],
        [("action", "x" * 10000)],
        [("cursor", "a"), ("cursor", "b")],
    ],
)
def test_invalid_filters_are_never_broadened(pairs):
    with pytest.raises(ValidationError):
        parse_audit_filters(pairs)


def test_cursor_integrity_expiry_and_filter_binding(history):
    h = history
    seed(h, 51)
    now = INSTANT
    service = AuditHistoryService(h.s.users.uow, cursor_key=b"test-key" * 4, clock=lambda: now)
    cursor = service.list_history(h.admin).next_cursor
    for invalid in ("bad", cursor[:-1] + ("0" if cursor[-1] != "0" else "1"), "x" * 2049):
        with pytest.raises(ValidationError, match="invalid or expired"):
            service.list_history(h.admin, cursor=invalid)
    with pytest.raises(ValidationError):
        service.list_history(h.admin, AuditFilters(object_type="user"), cursor)
    with h.sessions.begin() as session:
        session.get(User, h.reader).role = "admin"
    with pytest.raises(ValidationError):
        service.list_history(h.reader, cursor=cursor)
    now += timedelta(seconds=CURSOR_LIFETIME)
    with pytest.raises(ValidationError):
        service.list_history(h.admin, cursor=cursor)


def test_service_authorization_and_read_only(history):
    h = history
    event_id = seed(h, 1)[0]
    before = state(h)
    for actor in (None, "", "invalid", str(uuid4()), h.reader):
        with pytest.raises(AuthorizationError):
            h.service.list_history(actor, cursor="invalid")
        for target in (event_id, str(uuid4()), "invalid"):
            with pytest.raises(AuthorizationError):
                h.service.get_event(actor, target)
    assert h.service.get_event(h.admin, event_id).id == event_id
    assert len(h.service.list_history(h.admin).items) == 1
    with pytest.raises(NotFoundError):
        h.service.get_event(h.admin, str(uuid4()))
    assert state(h) == before
    for deleted in (False, True):
        with h.sessions.begin() as session:
            user = session.get(User, h.admin)
            user.is_active, user.is_deleted = False, deleted
        with pytest.raises(AuthorizationError):
            h.service.list_history(h.admin)
        with pytest.raises(AuthorizationError):
            h.service.get_event(h.admin, event_id)


@pytest.mark.parametrize("permission", ["viewer", "editor", "admin"])
def test_dataset_and_team_grants_do_not_authorize(history, permission):
    h = history
    team = h.s.teams.create_team(h.admin, "Owners")
    dataset = h.s.datasets.create_dataset(h.admin, "sample", "Sample", "Secret", team.id)
    h.s.memberships.create_membership(h.admin, h.reader, team.id)
    h.s.grants.create_grant(h.admin, "team", team.id, "dataset", dataset.id, permission)
    target = h.service.list_history(h.admin).items[0].id
    with pytest.raises(AuthorizationError):
        h.service.list_history(h.reader)
    with pytest.raises(AuthorizationError):
        h.service.get_event(h.reader, target)
    browser = client(h, h.reader)
    for path in (
        "/settings/audit-log",
        f"/settings/audit-log/{target}",
        f"/settings/audit-log/{uuid4()}",
        "/settings/audit-log?action=invalid",
    ):
        assert browser.get(path, base_url="https://localhost").status_code == 403


def test_all_contracts_have_safe_presentations(history):
    h = history
    require_complete_presentation()
    assert len(EVENTS) == len(ACTION_LABELS) == 49
    assert ACTION_LABELS[("dataset", "dataset.version_diff_viewed")] == "Dataset changes reviewed"
    assert ACTION_LABELS[("dataset", "hierarchy.node_created")] == "Hierarchy node created"
    assert ACTION_LABELS[("dataset", "hierarchy.node_updated")] == "Hierarchy node updated"
    assert ACTION_LABELS[("dataset", "hierarchy.values_updated")] == "Hierarchy node values updated"
    assert set(OBJECTS) == {kind for kind, _ in EVENTS}
    assert not SYSTEM_ACTORS and {c.actor for c in EVENTS.values()} == {
        "required",
        "unauthenticated",
        "optional",
    }
    actions = dict(ACTION_LABELS)
    actions.pop(next(iter(actions)))
    with pytest.raises(AuditContractError, match="Missing audit action"):
        require_complete_presentation(actions=actions)
    objects = dict(OBJECTS)
    objects.pop("dataset")
    with pytest.raises(AuditContractError, match="Missing audit object"):
        require_complete_presentation(objects=objects)
    for i, ((kind, action), contract) in enumerate(EVENTS.items(), 1):
        values = {
            "uuid": h.reader,
            "count": 12,
            "version_count": 12,
            "calendar_date": "2026-09-10",
            "subject": "user",
            "import_format": "CSV",
            "import_mode": "new_version",
            "export_format": "JSON",
            "dataset_name": "Historical Dataset name",
        }
        details = {key: values[formatter] for key, formatter in contract.context}
        eid = seed(h, 1, first=i, kind=kind, action=action, details=details)[0]
        if contract.actor == "unauthenticated":
            with h.sessions.begin() as session:
                session.get(AuditEvent, eid).actor_user_id = None
        view = h.service.get_event(h.admin, eid)
        if contract.actor == "unauthenticated":
            assert view.actor_label == "Unauthenticated"
        assert (view.object_type, view.action) == (kind, action)
        assert view.action_label == ACTION_LABELS[kind, action]
        assert len(view.context) == len(contract.context)


def test_historical_api_key_success_keeps_owner_presentation(history):
    h = history
    event_id = seed(
        h,
        1,
        actor=h.reader,
        kind="api_key",
        action="api_key.authenticated",
        target=str(uuid4()),
    )[0]
    view = h.service.get_event(h.admin, event_id)
    assert view.action_label == "API key authentication succeeded"
    assert view.actor_label == "User"
    assert view.actor_id == h.reader


@pytest.mark.parametrize(
    "value",
    ["<script>alert(1)</script>", {"secret": "token"}, ["password"], True, None, "x" * 10000, -1],
)
def test_unsafe_context_fails_closed(value):
    row = SimpleNamespace(
        actor_user_id=str(uuid4()),
        object_id=str(uuid4()),
        object_type="dataset",
        action="dataset.records_imported",
        details={"format": "CSV", "record_count": value, "rejected_row_count": 0},
    )
    assert safe_context(row) == ()


def test_privacy_current_names_and_html_escaping(history):
    h = history
    payload = '<img src=x onerror="alert(1)"> Café\u202e\x01'
    with h.sessions.begin() as session:
        session.get(User, h.reader).display_name = payload
    eid = seed(
        h,
        1,
        actor=h.reader,
        details={
            "password": "secret-token",
            "description": "secret-description",
            "values": {"record": "secret-value"},
        },
    )[0]
    before = state(h)["audit_events"]
    browser = client(h, h.admin)
    response = browser.get(f"/settings/audit-log/{eid}", base_url="https://localhost")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "&lt;img" in html and "<img src=x" not in html
    for hidden in (
        "secret-token",
        "secret-description",
        "secret-value",
        h.reader + "@",
        "\u202e",
        "\x01",
    ):
        assert hidden not in html
    assert "No additional details" in html
    with h.sessions.begin() as session:
        session.get(User, h.reader).display_name = "Renamed"
    assert h.service.get_event(h.admin, eid).actor_label == "Renamed"
    assert h.service.get_event(h.admin, eid).object_label == "Renamed"
    with h.sessions.begin() as session:
        user = session.get(User, h.reader)
        user.is_active, user.is_deleted = False, True
    view = h.service.get_event(h.admin, eid)
    assert view.actor_label == "Deleted user"
    assert view.object_label == "Deleted object"
    assert h.service.list_history(h.admin, AuditFilters(actor=h.reader)).items[0].id == eid
    assert state(h)["audit_events"] == before
    assert safe_name("x" * 201, "fallback") == "fallback"
    assert safe_name("\u202e\x00", "fallback") == "fallback"


def test_bounded_queries_many_actors_and_types(history):
    h = history
    with h.sessions.begin() as session:
        for i in range(60):
            uid = str(uuid4())
            session.add(
                User(
                    id=uid,
                    email=f"{uid}@example.com",
                    display_name=f"Actor {i}",
                    password_hash="unused",
                    role="user",
                )
            )
            session.flush()
            kind = tuple(OBJECTS)[i % len(OBJECTS)]
            action = next(a for k, a in EVENTS if k == kind)
            session.add(
                AuditEvent(
                    actor_user_id=uid,
                    object_id=str(uuid4()),
                    object_type=kind,
                    action=action,
                    details={"secret": "x" * 20000},
                )
            )
    queries = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)

    event.listen(h.engine, "before_cursor_execute", record)
    try:
        page = h.service.list_history(h.admin)
        assert len(page.items) == 50
        assert len(queries) <= 7
        assert all(q.lstrip().upper().startswith("SELECT") for q in queries)
        assert "LIMIT" in queries[1] and "OFFSET" not in queries[1].upper().replace("OFFSET ?", "")
        # REF-141 conditionally projects only bounded Dataset deletion snapshots.
        assert "CASE WHEN" in queries[1] and "length(" in queries[1]
        assert all(not item.context for item in page.items)
        queries.clear()
        detail = h.service.get_event(h.admin, page.items[0].id)
        assert not detail.context and len(queries) <= 4
    finally:
        event.remove(h.engine, "before_cursor_execute", record)


def test_object_resolvers_use_only_current_safe_names(history):
    h = history
    team = h.s.teams.create_team(h.admin, "Current team")
    dataset = h.s.datasets.create_dataset(
        h.admin, "current", "Current Dataset", "Secret description", team.id
    )
    field = h.s.dataset_fields.create_field(h.admin, dataset.key, "code", "Current field", "string")
    targets = {
        "team": (Team, team.id),
        "dataset": (Dataset, dataset.id),
        "dataset_field": (DatasetField, field.id),
    }
    events = {item.object_type: item for item in h.service.list_history(h.admin).items}
    before = state(h)["audit_events"]
    for kind, (model, uid) in targets.items():
        assert events[kind].object_label.startswith("Current")
        with h.sessions.begin() as session:
            session.get(model, uid).name = f"Renamed {kind}"
        view = h.service.get_event(h.admin, events[kind].id)
        assert view.object_label == f"Renamed {kind}"
    # A field can disappear without deleting its history; never fall back to Record values.
    with h.sessions.begin() as session:
        session.delete(session.get(DatasetField, field.id))
    assert h.service.get_event(h.admin, events["dataset_field"].id).object_label == "Deleted object"
    assert state(h)["audit_events"] == before


def test_unknown_contract_and_context_are_not_disclosed(history):
    h = history
    eid = seed(
        h,
        1,
        kind="<script>secret-kind</script>",
        action="secret-action",
        details={"secret": "hidden"},
    )[0]
    view = h.service.get_event(h.admin, eid)
    assert view.action == view.object_type == "Unavailable"
    assert view.object_label == "No name to display" and not view.context
    approved = seed(
        h,
        1,
        first=2,
        kind="dataset",
        action="dataset.records_imported",
        details={
            "format": "CSV",
            "record_count": 2,
            "rejected_row_count": 0,
            "password": "secret",
            "record_values": {"secret": "hidden"},
        },
    )[0]
    view = h.service.get_event(h.admin, approved)
    assert view.context == (("Format", "CSV"), ("Records imported", "2"), ("Rows rejected", "0"))


def test_http_filters_errors_and_no_audit_writes(history, monkeypatch):
    h = history
    ids = seed(h, 51)
    browser = client(h, h.admin)
    before = state(h)
    response = browser.get("/settings/audit-log?action=user:created", base_url="https://localhost")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    link = next(link for link in re.findall(r'href="([^"]+)"', html) if "cursor=" in link)
    query = parse_qs(urlsplit(html_module.unescape(link)).query)
    assert query["action"] == ["user:created"] and query["cursor"]
    assert 'aria-current="page"' in html and "Clear filters" in html
    assert "2026-03-29T00:59:59.123456+00:00" in html
    assert response.headers["Cache-Control"] == "no-store"
    for query in ("actor=bad", "actor=&actor=", "cursor=bad", "unknown=secret"):
        response = browser.get("/settings/audit-log?" + query, base_url="https://localhost")
        assert response.status_code == 400
        assert re.search(r"role=['\"]alert['\"]", response.get_data(as_text=True))
        assert ids[0] not in response.get_data(as_text=True)
    assert (
        browser.get(f"/settings/audit-log/{uuid4()}", base_url="https://localhost").status_code
        == 404
    )
    assert (
        browser.post(f"/settings/audit-log/{ids[0]}", base_url="https://localhost").status_code
        == 405
    )
    assert state(h) == before
    anonymous = client(h, None)
    response = anonymous.get(
        f"/settings/audit-log/{ids[0]}?secret=hidden", base_url="https://localhost"
    )
    assert response.status_code == 302 and "/login" in response.location
    assert "hidden" not in response.location
    with h.sessions.begin() as session:
        session.get(User, h.admin).is_active = False
    assert browser.get("/settings/audit-log", base_url="https://localhost").status_code == 302


def test_database_failure_is_neutral(history, monkeypatch):
    h = history
    browser = client(h, h.admin)

    def broken(*args, **kwargs):
        raise OperationalError("secret SQL", {}, Exception("secret credentials"))

    monkeypatch.setattr(SqlAlchemyAuditReadOperations, "_statement", broken)
    response = browser.get("/settings/audit-log", base_url="https://localhost")
    assert response.status_code == 503
    assert "secret" not in response.get_data(as_text=True)


def test_read_boundary_has_no_framework_or_database_imports():
    root = Path(__file__).parents[1] / "src/refrncr"
    for name in ("audit_history.py", "audit_presentation.py", "ports.py", "web.py"):
        tree = ast.parse((root / name).read_text())
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(n and n.startswith("sqlalchemy") for n in imports)
        if name != "web.py":
            assert not any(n and n.startswith("flask") for n in imports)


def test_chronology_query_plan(history):
    h = history
    seed(h, 10000)
    query = (
        "SELECT id,timestamp,actor_user_id,object_type,object_id,action,NULL AS details "
        "FROM audit_events WHERE (timestamp,id) < (:timestamp,:id) "
        "ORDER BY timestamp DESC,id DESC LIMIT 51"
    )
    prefix = "EXPLAIN QUERY PLAN " if h.engine.dialect.name == "sqlite" else "EXPLAIN "
    params = {
        "timestamp": INSTANT.replace(tzinfo=None).isoformat(sep=" ")
        if h.engine.dialect.name == "sqlite"
        else INSTANT,
        "id": str(UUID(int=5000)),
    }
    with h.engine.begin() as connection:
        connection.execute(text("ANALYZE audit_events"))
        indexed = "\n".join(
            str(tuple(row)) for row in connection.execute(text(prefix + query), params)
        )
        connection.execute(text("DROP INDEX ix_audit_events_timestamp_id"))
        unindexed = "\n".join(
            str(tuple(row)) for row in connection.execute(text(prefix + query + " "), params)
        )
        connection.execute(
            text("CREATE INDEX ix_audit_events_timestamp_id ON audit_events(timestamp,id)")
        )
    assert "ix_audit_events_timestamp_id" in indexed
    assert "SORT" not in indexed.upper() and "TEMP B-TREE" not in indexed.upper()
    assert "SORT" in unindexed.upper() or "TEMP B-TREE" in unindexed.upper()
    print(
        f"\n{h.engine.dialect.name} 10,000 events, 51-row projected continuation:\n"
        f"without index:\n{unindexed}\nwith index:\n{indexed}"
    )
