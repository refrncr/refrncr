"""Independent REF-39 sabotage, privilege, concurrency and read-failure regressions."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from test_audit_coverage import audit_db as audit_db
from test_audit_history import INSTANT, client, seed, state
from test_audit_history import history as history

from refrncr.audit import EVENTS, AuditContractError
from refrncr.audit_history import parse_audit_filters, parse_audit_time
from refrncr.audit_presentation import (
    ACTION_LABELS,
    ACTION_PRESENTATIONS,
    CONTEXT_LABELS,
    OBJECTS,
    ActionPresentation,
    ObjectPresentation,
    require_complete_presentation,
)
from refrncr.models import User
from refrncr.ports import AuditFilters, AuditReadUnavailable
from refrncr.services import AuthorizationError, ValidationError
from refrncr.web import SESSION_COOKIE


@pytest.mark.parametrize("sabotage", ["action", "type", "context", "rename", "resolver", "stale"])
def test_presentation_guard_sabotage(sabotage):
    events, actions, objects, labels = map(dict, (EVENTS, ACTION_LABELS, OBJECTS, CONTEXT_LABELS))
    contract = EVENTS["user", "created"]
    if sabotage == "action":
        events["user", "new_action"] = replace(contract, action="new_action")
    elif sabotage == "type":
        events["new_type", "created"] = replace(contract, object_type="new_type")
        actions["new_type", "created"] = "New type created"
    elif sabotage == "context":
        labels.pop("dataset_id")
    elif sabotage == "rename":
        events.pop(("user", "created"))
        events["user", "renamed_technical"] = replace(contract, action="renamed_technical")
    elif sabotage == "resolver":
        objects["dataset_record"] = ObjectPresentation("Record", "user")
    else:
        actions["user", "fictional"] = "Fictional"
    with pytest.raises(AuditContractError, match="audit"):
        require_complete_presentation(events, actions, objects, labels)


def test_guard_accepts_coherent_extension_and_ignores_unrelated_ui():
    events, actions, objects = map(dict, (EVENTS, ACTION_LABELS, OBJECTS))
    events["new_type", "created"] = replace(EVENTS["user", "created"], object_type="new_type")
    actions["new_type", "created"] = "New type created"
    objects["new_type"] = ObjectPresentation("New type", None)
    unrelated_ui = {"save": "Save changes"}
    presentations = dict(ACTION_PRESENTATIONS)
    presentations["new_type", "created"] = ActionPresentation("New type created", False)
    require_complete_presentation(events, actions, objects, presentations=presentations)
    assert unrelated_ui == {"save": "Save changes"}


@pytest.mark.parametrize(
    "offset",
    [
        "2026-03-28T20:59:59.123456-04:00",
        "2026-03-29T09:59:59.123456+09:00",
        "2026-03-29T00:59:59.123456Z",
    ],
)
def test_offset_filters_refer_to_same_instant(history, offset):
    expected = seed(history, 1)
    filters, _ = parse_audit_filters([("start", offset), ("end", "2026-03-29T01:00Z")])
    assert parse_audit_time(offset) == INSTANT
    assert [x.id for x in history.service.list_history(history.admin, filters).items] == expected


@pytest.mark.parametrize(
    "value",
    ["\u202e", "' OR 1=1 --", "<script>", "2026-01-01", "2026-01-01T00:00+24:00", "x" * 2050],
)
def test_hostile_filter_input_never_returns_rows(history, value):
    seed(history, 1)
    browser = client(history, history.admin)
    for key in ("actor", "action", "object_type", "start", "end", "cursor"):
        response = browser.get(
            "/settings/audit-log", query_string={key: value}, base_url="https://localhost"
        )
        assert response.status_code == 400
        assert b"data-panel-url" not in response.data
        assert response.headers["Cache-Control"] == "no-store"


def test_demoted_admin_and_forged_session_cannot_reuse_any_read(history):
    h = history
    ids = seed(h, 101)
    first = h.service.list_history(h.admin)
    second = h.service.list_history(h.admin, cursor=first.next_cursor)
    browser = client(h, h.admin)
    with h.sessions.begin() as session:
        session.get(User, h.admin).role = "user"
    before = state(h)
    for cursor in (None, first.next_cursor, second.previous_cursor):
        with pytest.raises(AuthorizationError):
            h.service.list_history(h.admin, AuditFilters(actor=h.reader), cursor)
        response = browser.get(
            "/settings/audit-log",
            query_string={"cursor": cursor} if cursor else {},
            base_url="https://localhost",
        )
        assert response.status_code == 403
    for target in (ids[0], str(uuid4()), "invalid"):
        with pytest.raises(AuthorizationError):
            h.service.get_event(h.admin, target)
        assert (
            browser.get(f"/settings/audit-log/{target}", base_url="https://localhost").status_code
            == 403
        )
    after = state(h)
    assert after | {"audit_events": before["audit_events"]} == before
    assert sum("security.authorization_denied" in row for row in after["audit_events"]) == 6
    browser.set_cookie(
        SESSION_COOKIE, json.dumps({"user_id": h.admin, "role": "admin"}), domain="localhost"
    )
    response = browser.get(f"/settings/audit-log/{ids[0]}", base_url="https://localhost")
    assert response.status_code == 302 and "/login" in response.location


def test_cursor_direction_tampering_and_truncation(history):
    h = history
    seed(h, 51)
    token = h.service.list_history(h.admin).next_cursor
    encoded, signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    payload["newer"] = True
    changed = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        + "."
        + signature
    )
    for invalid in (changed, token[:50], token[:-1], token + "0"):
        with pytest.raises(ValidationError):
            h.service.list_history(h.admin, cursor=invalid)


def test_concurrent_writer_during_page_read_preserves_original_set(history):
    h = history
    expected = seed(h, 151)
    first = h.service.list_history(h.admin)
    reached, committed = Event(), Event()

    def write():
        assert reached.wait(10)
        seed(h, 10, first=1000, timestamp=INSTANT + timedelta(seconds=1))
        committed.set()

    def pause(_conn, _cursor, statement, _parameters, _context, _many):
        if (
            statement.lstrip().startswith("SELECT")
            and "audit_events" in statement
            and not reached.is_set()
        ):
            reached.set()
            assert committed.wait(10)

    event.listen(h.engine, "before_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(write)
            page = h.service.list_history(h.admin, cursor=first.next_cursor)
            seen = [x.id for x in first.items]
            while True:
                seen.extend(x.id for x in page.items)
                if not page.next_cursor:
                    break
                page = h.service.list_history(h.admin, cursor=page.next_cursor)
            writer.result(timeout=10)
    finally:
        event.remove(h.engine, "before_cursor_execute", pause)
    assert seen == expected
    assert len(seen) == len(set(seen))


def test_backdated_insert_has_documented_keyset_not_snapshot_semantics(history):
    h = history
    original = seed(h, 101)
    page = h.service.list_history(h.admin)
    late = seed(h, 1, first=1000, timestamp=INSTANT - timedelta(seconds=1))
    seen = [x.id for x in page.items]
    while page.next_cursor:
        page = h.service.list_history(h.admin, cursor=page.next_cursor)
        seen.extend(x.id for x in page.items)
    assert seen == original + late


@pytest.mark.parametrize("kind", list(OBJECTS))
def test_object_id_cannot_resolve_another_type(history, kind):
    h = history
    action = next(action for object_type, action in EVENTS if object_type == kind)
    eid = seed(h, 1, kind=kind, action=action, target=h.reader)[0]
    expected = (
        "User"
        if kind == "user"
        else ("Deleted object" if OBJECTS[kind].resolver else "No name to display")
    )
    assert h.service.get_event(h.admin, eid).object_label == expected


@pytest.mark.parametrize("stage", ["list", "detail", "actor", "object"])
def test_read_failure_at_each_stage_is_neutral_and_never_commits(history, stage):
    h = history
    eid = seed(h, 1)[0]
    browser = client(h, h.admin)
    before = state(h)
    commits = []

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        target = {
            "list": "audit_events",
            "detail": "audit_events",
            "actor": "users.display_name",
            "object": "users.display_name",
        }[stage]
        if target in statement and (stage in {"list", "detail"} or "users.email" not in statement):
            if (
                stage == "object"
                and "users.id IN" in statement
                and not getattr(fail, "actor_seen", False)
            ):
                fail.actor_seen = True
                return
            raise OperationalError("secret SQL", {}, Exception("secret token and stack trace"))

    def on_commit(_conn):
        commits.append(True)

    event.listen(h.engine, "before_cursor_execute", fail)
    event.listen(h.engine, "commit", on_commit)
    try:
        with pytest.raises(AuditReadUnavailable):
            if stage == "detail":
                h.service.get_event(h.admin, eid)
            else:
                h.service.list_history(h.admin)
        fail.actor_seen = False
        path = f"/settings/audit-log/{eid}" if stage == "detail" else "/settings/audit-log"
        response = browser.get(path, base_url="https://localhost")
        assert response.status_code == 503
        assert b"secret" not in response.data and b"OperationalError" not in response.data
        assert not commits
    finally:
        event.remove(h.engine, "before_cursor_execute", fail)
        event.remove(h.engine, "commit", on_commit)
    assert state(h) == before
