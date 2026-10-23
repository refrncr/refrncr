"""Reusable test helpers for browser-facing functional tests."""

import csv
import io
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from types import SimpleNamespace
from uuid import UUID

import pytest
from dataset_helpers import create_owned_dataset
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool
from werkzeug.datastructures import MultiDict

from refrncr import login_security
from refrncr.app import create_community_services
from refrncr.clock import SYSTEM_CLOCK
from refrncr.dataset_diff import DatasetDiffService
from refrncr.dataset_versions import DatasetVersionService
from refrncr.datatypes import DATATYPES, get_datatype
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.hierarchy import HierarchyService
from refrncr.models import AuditEvent, Dataset, User
from refrncr.ports import RecordValue, StoredRecord
from refrncr.services import ResourceTypeRegistry
from refrncr.version_contracts import APPLICATION_TIMEZONE
from refrncr.web import SESSION_COOKIE, create_flask_app

HTTPS = "https://localhost"
SECURITY_HTTPS = "https://community.example"
PASSWORD = "correct horse battery staple"
LoginAttemptLimiter = login_security.LoginAttemptLimiter


class MutableClock:
    def __init__(self, value: datetime | None = None):
        self.value = SYSTEM_CLOCK.now() if value is None else value

    def now(self) -> datetime:
        return self.value

    def today(self) -> date:
        return self.value.astimezone(APPLICATION_TIMEZONE).date()

    def set(self, value: datetime) -> None:
        self.value = value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


MUTATIONS = (
    "UserService.create_user",
    "UserService.change_email",
    "UserService.disable_user",
    "UserService.enable_user",
    "UserService.delete_user",
    "TeamService.create_team",
    "TeamService.rename_team",
    "TeamService.delete_team",
    "MembershipService.create_membership",
    "MembershipService.delete_membership",
    "GrantService.create_grant",
    "GrantService.change_permission",
    "GrantService.delete_grant",
    "DatasetVersionService.publish",
    "DatasetVersionService.cancel",
    "DatasetService.create_dataset",
    "DatasetService.delete_dataset",
    "DatasetService.change_owner",
    "DatasetService.update_dataset",
    "DatasetService.grant_export_access",
    "DatasetService.revoke_export_access",
    "DatasetFieldService.create_field",
    "DatasetFieldService.update_field",
    "DatasetFieldService.update_field_and_business_key_membership",
    "DatasetFieldService.delete_field",
    "DatasetFieldService.replace_order",
    "DatasetFieldService.move_field",
    "DatasetBusinessKeyService.set_business_key",
    "DatasetRecordService.create_record",
    "DatasetRecordService.update_record",
    "DatasetRecordService.delete_record",
    "DatasetRecordService.restore_record",
    "DatasetRecordImportService.confirm",
)


def _dataset_browser():
    engine = create_database_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Administrator", PASSWORD)
    services.teams.create_team(admin.id, "Test data domain")
    client = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
    ).test_client()
    return factory, services, admin, client


@pytest.fixture
def dataset_browser():
    return _dataset_browser()


def request(client, method, path, **kwargs):
    return getattr(client, method)(path, base_url=HTTPS, **kwargs)


class _Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def html_elements(html):
    """Return parsed HTML elements as ``(tag, attributes)`` pairs."""
    return _Elements(html).items


class _Text(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.parts = []
        self.feed(html)

    def handle_data(self, data):
        self.parts.append(data)


def html_text(html):
    """Return text nodes from parsed HTML, excluding markup."""
    return "".join(_Text(html).parts)


class _TagTexts(HTMLParser):
    def __init__(self, html, wanted):
        super().__init__()
        self.wanted = wanted
        self.values = []
        self._current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == self.wanted:
            self._current = []

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag == self.wanted and self._current is not None:
            self.values.append("".join(self._current).strip())
            self._current = None


def html_tag_texts(html, tag):
    """Return text content for each parsed element with the given tag."""
    return _TagTexts(html, tag).values


def get(c, suffix="", **kwargs):
    return c.client.get(
        "/api/v1/datasets" + suffix, headers=kwargs.pop("headers", c.headers), **kwargs
    )


def csrf_token(response):
    text = response.get_data(as_text=True) if hasattr(response, "get_data") else response.text
    match = re.search(r"name='csrf_token' value='([^']+)'", text)
    assert match is not None
    return match.group(1)


def configured_web(limiter, admission=None):
    engine = create_database_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    services = create_community_services(create_session_factory(engine), ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    client = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        login_limiter=limiter,
        authentication_admission=admission,
    ).test_client()
    token = csrf_token(client.get("/setup", base_url=SECURITY_HTTPS))
    response = client.post(
        "/setup",
        base_url=SECURITY_HTTPS,
        data={
            "email": "admin@example.com",
            "display_name": "Administrator",
            "password": PASSWORD,
            "csrf_token": token,
        },
    )
    assert response.status_code == 302
    return services, client


def post_login(client, email, password, **kwargs):
    token = csrf_token(client.get("/login", base_url=SECURITY_HTTPS, **kwargs))
    return client.post(
        "/login",
        base_url=SECURITY_HTTPS,
        data={"email": email, "password": password, "csrf_token": token},
        **kwargs,
    )


def body(response):
    return response.get_data(as_text=True)


def form_csrf(response):
    match = re.search(r"name='csrf_token' value='([^']+)'", body(response))
    assert match is not None
    return match.group(1)


def login(client, email="admin@example.com", password=PASSWORD):
    login_form = request(client, "get", "/login")
    response = request(
        client,
        "post",
        "/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": form_csrf(login_form),
        },
    )
    assert response.status_code == 302


def session_csrf(client, services):
    cookie = client.get_cookie(SESSION_COOKIE)
    assert cookie is not None
    resolved = services.sessions.resolve_with_session(cookie.value)
    assert resolved is not None
    return resolved[1].csrf_token


def create_dataset(services, admin, key, name=None, description=""):
    return create_owned_dataset(
        services.datasets,
        admin.id,
        key,
        name or key.replace("-", " ").title(),
        description,
    )


def create_field(
    services,
    actor,
    dataset,
    key="field",
    name="Field",
    data_type="string",
    is_required=False,
    is_unique=False,
):
    return services.dataset_fields.create_field(
        actor.id,
        dataset.key,
        key,
        name,
        data_type,
        is_required,
        is_unique,
    )


def audits(context):
    with context.sessions() as session:
        return list(
            session.scalars(select(AuditEvent).order_by(AuditEvent.timestamp, AuditEvent.id))
        )


def context_from_audit_db(audit_db):
    engine, sessions, services = audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = services.users.create_user(admin.id, "user@example.com", "User", "valid password")
    owner = services.teams.create_team(admin.id, "Domain")
    other = services.teams.create_team(admin.id, "Other")
    dataset = services.datasets.create_dataset(
        admin.id, "audit", "Business name", "Secret description", owner.id
    )
    return SimpleNamespace(
        engine=engine,
        sessions=sessions,
        s=services,
        a=admin,
        u=user,
        owner=owner,
        other=other,
        d=dataset,
    )


def snapshot(context, *, include_visits=True):
    with context.sessions() as session:
        return {
            table.name: sorted(
                repr(tuple(row))
                for row in session.execute(
                    select(table).where(table.c.object_type != "security")
                    if table.name == "audit_events"
                    else select(table)
                )
            )
            for table in Base.metadata.sorted_tables
            if include_visits or table.name != "dataset_visits"
        }


INSTANT = datetime(2026, 3, 29, 0, 59, 59, 123456, tzinfo=UTC)


def seed(
    history,
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
    with history.sessions.begin() as session:
        session.add_all(
            [
                AuditEvent(
                    id=str(UUID(int=i)),
                    timestamp=timestamp,
                    actor_user_id=actor or history.admin,
                    object_type=kind,
                    object_id=target or history.reader,
                    action=action,
                    details=details,
                )
                for i in range(first, first + count)
            ]
        )
    return [str(UUID(int=i)) for i in reversed(range(first, first + count))]


def audit_history_state(history):
    with history.sessions() as session:
        return {
            table.name: sorted(repr(tuple(row)) for row in session.execute(select(table)))
            for table in Base.metadata.sorted_tables
        }


def audit_history_client(history, uid):
    app = create_flask_app(
        history.s.users,
        history.s.authentication,
        history.s.sessions,
        history.s.teams,
        history.s.memberships,
        audit_history=history.service,
    )
    app.testing = True
    result = app.test_client()
    if uid:
        with history.sessions() as session:
            user = session.get(User, uid)
        token, _ = history.s.sessions.create(user)
        result.set_cookie(SESSION_COOKIE, token, domain="localhost")
    return result


def governance_state(factory, dataset):
    with factory() as session:
        row = session.get(Dataset, dataset.id)
        return row.owner_team_id, row.updated_at, session.scalar(select(func.count(AuditEvent.id)))


NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def setup(c, *, populated=True):
    c.service = DatasetVersionService(
        c.s.datasets.uow, c.s.authorization.datasets, clock=MutableClock(NOW)
    )
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "code", "Code", "string", True, True, True)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "label", "Label", "string")
    if populated:
        c.record = c.s.dataset_records.create_record(
            c.a.id, c.d.key, {"code": "a", "label": "Café"}
        )
    return c.service


def revision(c):
    return c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).revision


def publish(c, *, day=None, actor=None, expected=None):
    automatic_day = day is None
    if automatic_day:
        active_dates = [v.effective_date for v in versions(c) if v.cancelled_at is None]
        day = max(active_dates) + timedelta(days=1) if active_dates else NOW.date()
    result = c.service.publish(
        actor or c.a.id,
        c.d.id,
        day,
        "Release",
        revision(c) if expected is None else expected,
    )
    if automatic_day:
        c.service.clock.set(NOW + timedelta(days=(day - NOW.date()).days))
    return result


def versions(c):
    with c.s.datasets.uow() as uow:
        return uow.versions.list_for_dataset(c.d.id)


def content(c, version):
    with c.s.datasets.uow() as uow:
        return uow.versions.content(version)


def configured(c):
    setup(c)
    c.service = c.s.dataset_versions
    c.service.clock = MutableClock(NOW)


def dataset_version_client(c, actor=None):
    actor = actor or c.a
    browser = create_flask_app(
        c.s.users,
        c.s.authentication,
        c.s.sessions,
        c.s.teams,
        c.s.memberships,
        authorization=c.s.authorization,
        datasets=c.s.datasets,
        dataset_fields=c.s.dataset_fields,
        dataset_business_keys=c.s.dataset_business_keys,
        dataset_records=c.s.dataset_records,
        record_imports=c.s.record_imports,
        dataset_versions=c.service,
    ).test_client()
    token, session = c.s.sessions.create(actor)
    browser.set_cookie(SESSION_COOKIE, token, domain="localhost", secure=True)
    return browser, session.csrf_token


def payload(c, csrf, *, day=None, note="Release"):
    return {
        "csrf_token": csrf,
        "dataset_id": c.d.id,
        "confirmed": "yes",
        "expected_revision": str(c.s.datasets.get_dataset(c.a.id, c.d.key).revision),
        "effective_date": (day or NOW.date()).isoformat(),
        "release_note": note,
    }


def exported_rows(artifact):
    if artifact.extension == "json":
        return json.load(artifact.content)
    if artifact.extension == "csv":
        return list(csv.DictReader(io.StringIO(artifact.content.read().decode("utf-8-sig"))))
    workbook = load_workbook(artifact.content, read_only=True, data_only=False)
    try:
        rows = list(workbook.active.values)
        return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]
    finally:
        workbook.close()


def config_for(connection):
    from pathlib import Path

    from alembic.config import Config

    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/refrncr/migrations")
    )
    config.attributes["connection"] = connection
    return config


def migrate(engine, revision, mapping=None, downgrade=False):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/refrncr/migrations")
    )
    config.attributes["dataset_owner_mapping"] = mapping or {}
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def active(c):
    with c.s.datasets.uow() as uow:
        return uow.concepts.get(c.d.id)


def node(c, name, parent=None):
    return c.s.hierarchy.create_node(c.a.id, c.d.key, name, parent, expected_revision=revision(c))


def prepare(c):
    c.d = c.s.datasets.create_dataset(
        c.a.id, "hierarchy", "Hierarchy", "", c.owner.id, kind="hierarchy"
    )
    c.service = DatasetVersionService(
        c.s.datasets.uow, c.s.authorization.datasets, clock=MutableClock(NOW)
    )
    c.diff = DatasetDiffService(c.service)
    return node(c, "Root")


def setup_dataset(services, admin, types=DATATYPES):
    dataset = create_owned_dataset(services.datasets, admin.id, "types", "Types", "")
    services.dataset_fields.create_field(
        admin.id, dataset.key, "id", "ID", "string", True, False, True
    )
    fields = {}
    for type_id in types:
        fields[type_id] = services.dataset_fields.create_field(
            admin.id, dataset.key, type_id, get_datatype(type_id).label, type_id, False, True
        )
    return dataset, fields


def configured_dataset(services, admin, key="records"):
    dataset = create_owned_dataset(services.datasets, admin.id, key, "Records", "")
    fields = {}
    for field_key, data_type, required, unique in (
        ("code", "string", True, False),
        ("amount", "number", True, False),
        ("active", "boolean", False, False),
        ("day", "date", False, True),
    ):
        fields[field_key] = services.dataset_fields.create_field(
            admin.id,
            key,
            field_key,
            field_key.title(),
            data_type,
            is_required=required,
            is_unique=unique,
        )
    services.dataset_business_keys.set_business_key(admin.id, key, ("code", "amount"))
    return dataset, fields


def field(key, data_type, position):
    return SimpleNamespace(key=key, data_type=data_type, display_position=position)


FIELDS = tuple(
    field(key, data_type, position)
    for position, (key, data_type) in enumerate(
        (
            ("code", "string"),
            ("amount", "number"),
            ("enabled", "boolean"),
            ("effective", "date"),
            ("optional", "string"),
        ),
        1,
    )
)


def stored(record_id="r1", code='Å, "quoted"\nline'):
    values = (
        RecordValue("f1", "code", "string", code, b"s" + code.encode(), False),
        RecordValue("f2", "amount", "number", Decimal("123.45"), b"n123.45", False),
        RecordValue("f3", "enabled", "boolean", True, b"b1", False),
        RecordValue("f4", "effective", "date", date(2026, 9, 4), b"d2026-09-04", False),
    )
    return StoredRecord(record_id, "dataset", values, 7)


def hierarchy_fixture(services, admin):
    dataset = create_owned_dataset(
        services.datasets, admin.id, "organisation", "Organisation", "", kind="hierarchy"
    )
    hierarchy = HierarchyService(services.datasets.uow, services.authorization)

    def add(name, parent=None):
        current = services.datasets.get_dataset(admin.id, dataset.key).revision
        return hierarchy.create_node(admin.id, dataset.key, name, parent, expected_revision=current)

    root = add("Executive")
    branch = add("Marketing", root.id)
    child = add("Content", branch.id)
    sibling = add("HR", root.id)
    return dataset, hierarchy, root, branch, child, sibling


def panel_revision(response):
    return re.search(r"name='expected_revision' value='(\d+)'", body(response)).group(1)


def optional_field(wizard, key):
    wizard.services.dataset_fields.create_field(
        wizard.admin.id,
        wizard.dataset.key,
        key,
        key.title(),
        "boolean" if key == "active" else "date",
    )


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.data = MultiDict()
        self.in_form = False
        self.current = None
        self.first = None
        self.selected = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = "data-import-mapping" in attrs
        if not self.in_form:
            return
        if (
            tag == "input"
            and attrs.get("name")
            and "disabled" not in attrs
            and (attrs.get("type") not in {"radio", "checkbox"} or "checked" in attrs)
        ):
            self.data.add(attrs["name"], attrs.get("value", ""))
        if tag == "select":
            self.current = attrs.get("name") if "disabled" not in attrs else None
            self.first = self.selected = None
        if tag == "option" and self.current:
            value = attrs.get("value", "")
            if self.first is None:
                self.first = value
            if "selected" in attrs:
                self.selected = value

    def handle_endtag(self, tag):
        if tag == "select" and self.current:
            self.data.add(self.current, self.selected or self.first or "")
            self.current = None


CASES = [
    ("string", " Cafe\u0301 ", "Café", "Café"),
    (
        "integer",
        "000123456789012345678901234567890",
        "123456789012345678901234567890",
        123456789012345678901234567890,
    ),
    (
        "number",
        "12345678901234567890.123456789012345678",
        "12345678901234567890.123456789012345678",
        Decimal("12345678901234567890.123456789012345678"),
    ),
    ("float", "1.2345678901234567e2", "123.45678901234567", 123.45678901234567),
    ("boolean", "false", "false", False),
    ("date", "2024-02-29", "2024-02-29", date(2024, 2, 29)),
    ("time", "23:59:59.120000", "23:59:59.12", time(23, 59, 59, 120000)),
    (
        "datetime",
        "2024-02-29T01:02:03.000001",
        "2024-02-29T01:02:03.000001",
        datetime(2024, 2, 29, 1, 2, 3, 1),  # noqa: DTZ001 - naive local Excel value fixture
    ),
    (
        "timestamp",
        "2024-03-01T00:30:00.123400+01:00",
        "2024-02-29T23:30:00.1234Z",
        datetime(2024, 2, 29, 23, 30, 0, 123400, tzinfo=UTC),
    ),
]
