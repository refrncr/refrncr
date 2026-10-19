"""Shared pytest policy and collection fixtures."""

import os
import re
import uuid
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type
from dataset_helpers import create_owned_dataset
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from support import NOW, MutableClock, _dataset_browser, publish, setup
from werkzeug.datastructures import MultiDict

from refrncr.api_keys import ApiKeyService
from refrncr.app import create_application, create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import Dataset, DatasetRecord, User
from refrncr.services import ResourceTypeRegistry
from refrncr.web import create_flask_app

POSTGRES_SKIP_REASON = "PostgreSQL tests require REFRNCR_TEST_POSTGRES_URL"
SLOW_NODEIDS = set()
UNMARKED_SLOW_TESTS = []
TEST_PASSWORD_HASHER = PasswordHasher(
    time_cost=1,
    memory_cost=8 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        help="include tests marked slow (CI includes them automatically)",
    )


@pytest.fixture(autouse=True)
def fast_password_hasher(monkeypatch):
    """Keep ordinary tests cheap while production parameters remain tested explicitly."""
    monkeypatch.setattr("refrncr.services.PASSWORD_HASHER", TEST_PASSWORD_HASHER)


def pytest_runtest_makereport(item, call):
    if call.when != "call" or call.duration <= 2 or item.get_closest_marker("slow"):
        return
    _record_unmarked_slow_test(item.nodeid, call.duration)


def pytest_runtest_logreport(report):
    """Collect call durations forwarded from xdist workers in the controller process."""
    if report.when == "call" and report.duration > 2 and "slow" not in report.keywords:
        _record_unmarked_slow_test(report.nodeid, report.duration)


def _record_unmarked_slow_test(nodeid, duration):
    if not any(existing_nodeid == nodeid for existing_nodeid, _ in UNMARKED_SLOW_TESTS):
        UNMARKED_SLOW_TESTS.append((nodeid, duration))


def pytest_sessionfinish(session, exitstatus):
    del exitstatus
    offenders = UNMARKED_SLOW_TESTS
    if not offenders:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("!", "unmarked tests exceeding 2 seconds")
        for nodeid, duration in sorted(offenders, key=lambda entry: entry[1], reverse=True):
            reporter.write_line(f"{duration:.2f}s {nodeid}")


@pytest.fixture
def dataset_browser():
    return _dataset_browser()


def _build_api(c):
    setup(c)
    with c.sessions.begin() as session:
        session.get(Dataset, c.d.id).updated_at = NOW - timedelta(seconds=1)
    c.version = publish(c)
    with c.sessions.begin() as session:
        session.get(Dataset, c.d.id).updated_at = NOW
    c.now = MutableClock(NOW)
    c.s.users.uow.clock = c.now
    c.keys = ApiKeyService(c.s.users.uow, pepper=b"disposable-ref107-test-pepper!!!!!", clock=c.now)
    c.key = c.keys.create_key(c.u.id, "Read integration")
    c.headers = {"Authorization": "Bearer " + c.key.secret}
    c.app = create_application(
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
        dataset_versions=c.service,
        api_keys=c.keys,
    )
    with TestClient(c.app, base_url="https://localhost", raise_server_exceptions=False) as client:
        c.client = client
        yield c


@pytest.fixture
def api(context):
    yield from _build_api(context)


@pytest.fixture
def database_api(database_context):
    yield from _build_api(database_context)


@pytest.fixture
def import_context():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin(
        "admin@example.com", "Admin", "correct horse battery staple"
    )
    dataset = create_owned_dataset(services.datasets, admin.id, "imports", "Imports", "")
    for key, name, data_type, required, unique in (
        ("code", "Code", "string", True, False),
        ("amount", "Amount", "number", True, False),
        ("active", "Active", "boolean", False, False),
        ("day", "Day", "date", False, True),
    ):
        services.dataset_fields.create_field(
            admin.id, dataset.key, key, name, data_type, is_required=required, is_unique=unique
        )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code", "amount"))
    return factory, services, admin, dataset


@pytest.fixture
def postgresql_import_context():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"record_import_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        services.users.bootstrap_organisation()
        admin = services.users.create_initial_admin(
            "admin@example.com", "Admin", "correct horse battery staple"
        )
        dataset = create_owned_dataset(services.datasets, admin.id, "imports", "Imports", "")
        for key in ("code", "amount"):
            services.dataset_fields.create_field(
                admin.id,
                dataset.key,
                key,
                key.title(),
                "string" if key == "code" else "number",
                is_required=True,
            )
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code", "amount"))
        yield factory, services, admin, dataset
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        administration_engine.dispose()


@pytest.fixture
def wizard(import_context):
    factory, services, admin, dataset = import_context
    for key in ("active", "day"):
        services.dataset_fields.delete_field(admin.id, dataset.key, key)
    app = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        dataset_records=services.dataset_records,
        record_imports=services.record_imports,
    )
    app.testing = True
    client = app.test_client()
    login = client.get("/login", base_url="https://localhost")
    csrf = login.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    client.post(
        "/login",
        base_url="https://localhost",
        data={"email": admin.email, "password": "correct horse battery staple", "csrf_token": csrf},
    )
    root = f"/datasets/{dataset.key}/records/import"
    page = client.get(root, base_url="https://localhost")
    csrf = re.search("name='csrf_token' value='([^']+)'", page.text)[1]

    class Browser:
        def get(self, path):
            page = client.get(path, base_url="https://localhost", follow_redirects=True)
            nonce = re.search("name='confirmation' value='([^']+)'", page.text)
            if nonce:
                self.confirmation = nonce[1]
            return page

        def post(self, path, data=None):
            values = MultiDict(data.items() if isinstance(data, dict) else data or [])
            values.setdefault("csrf_token", csrf)
            if path.endswith("/confirm"):
                values.setdefault("confirmation", getattr(self, "confirmation", "not-confirmed"))
            return client.post(
                path, base_url="https://localhost", data=values, follow_redirects=True
            )

        def upload(self, content=b"code,amount\nA,1\n", filename="records.csv"):
            page = self.post(root, {"file": (BytesIO(content), filename)})
            assert page.status_code == 200, page.text
            self.token = page.request.path.split("/import/")[1].split("/step/")[0]
            self.path = f"{root}/{self.token}"
            return page

        def preview(self, mapping=("code", "amount")):
            return self.post(
                self.path + "/preview",
                {
                    f"mapping_{i}": value if value is not None else "__ignored__"
                    for i, value in enumerate(mapping)
                },
            )

        def count(self):
            with factory() as session:
                return session.scalar(select(func.count()).select_from(DatasetRecord))

    browser = Browser()
    browser.root, browser.client, browser.csrf = root, client, csrf
    browser.services, browser.admin, browser.dataset = services, admin, dataset
    return browser


def _build_audit_db(database, tmp_path):
    administration = None
    if database == "postgresql":
        url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
        administration = create_database_engine(url)
        schema = f"audit_{uuid.uuid4().hex}"
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    else:
        url = f"sqlite:///{tmp_path / 'audit.db'}"
    engine = create_database_engine(url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    registry = ResourceTypeRegistry()
    registry.register("document", lambda key: key == "one")
    services = create_community_services(sessions, registry)
    yield engine, sessions, services
    engine.dispose()
    if administration:
        with administration.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()


@pytest.fixture
def audit_db(tmp_path):
    yield from _build_audit_db("sqlite", tmp_path)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def database_audit_db(request, tmp_path):
    yield from _build_audit_db(request.param, tmp_path)


def _audit_context(database):
    engine, sessions, services = database
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


@pytest.fixture
def context(audit_db):
    return _audit_context(audit_db)


@pytest.fixture
def database_context(database_audit_db):
    return _audit_context(database_audit_db)


def _audit_history(database):
    engine, sessions, services = database
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


@pytest.fixture
def history(audit_db):
    return _audit_history(audit_db)


@pytest.fixture
def database_history(database_audit_db):
    return _audit_history(database_audit_db)


@pytest.fixture
def record_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin(
        "admin@example.com", "Admin", "correct horse battery staple"
    )
    return engine, factory, services, admin


@pytest.fixture
def postgresql_record_services():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"dataset_records_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        services.users.bootstrap_organisation()
        admin = services.users.create_initial_admin(
            "admin@example.com", "Admin", "correct horse battery staple"
        )
        yield engine, factory, services, admin
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        administration_engine.dispose()


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def database_record_services(request):
    return request.getfixturevalue(
        "record_services" if request.param == "sqlite" else "postgresql_record_services"
    )


def _build_governance(database, tmp_path):
    administration = None
    if database == "postgresql":
        url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
        administration = create_database_engine(url)
        schema = f"governance_{uuid.uuid4().hex}"
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    else:
        url = f"sqlite:///{tmp_path / 'governance.db'}"
    engine = create_database_engine(url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = services.users.create_user(admin.id, "user@example.com", "User", "valid password")
    owner = services.teams.create_team(admin.id, "Data domain")
    other = services.teams.create_team(admin.id, "Other domain")
    dataset = services.datasets.create_dataset(admin.id, "countries", "Countries", "", owner.id)
    yield engine, factory, services, admin, user, owner, other, dataset
    engine.dispose()
    if administration:
        with administration.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()


@pytest.fixture
def governance(tmp_path):
    yield from _build_governance("sqlite", tmp_path)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def database_governance(request, tmp_path):
    yield from _build_governance(request.param, tmp_path)


def pytest_collection_modifyitems(config, items):
    """Apply local slow-test policy and skip PostgreSQL without a configured database."""
    run_slow = config.getoption("--run-slow") or os.environ.get("CI", "").lower() == "true"
    SLOW_NODEIDS.update(
        item.nodeid for item in items if item.get_closest_marker("slow") is not None
    )
    postgres_configured = bool(os.environ.get("REFRNCR_TEST_POSTGRES_URL"))
    for item in items:
        if item.get_closest_marker("slow") and not run_slow:
            item.add_marker(pytest.mark.skip(reason="slow tests require --run-slow or CI"))
        if item.get_closest_marker("postgres") is None:
            continue
        if not postgres_configured:
            item.add_marker(pytest.mark.skip(reason=POSTGRES_SKIP_REASON))
