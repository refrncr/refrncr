import os
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from dataset_helpers import create_owned_dataset
from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import make_url

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import (
    AuditEvent,
    Dataset,
    DatasetBusinessKeyField,
    DatasetField,
    Grant,
    ImmutableDatasetKeyError,
    User,
)
from refrncr.persistence import (
    SqlAlchemyDatasetOperations,
    SqlAlchemyGrantOperations,
    SqlAlchemyUnitOfWork,
    SqlAlchemyUnitOfWorkFactory,
)
from refrncr.ports import PersistenceConflictError
from refrncr.services import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ResourceTypeRegistry,
    ValidationError,
)


@pytest.fixture
def dataset_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    resources = ResourceTypeRegistry()
    services = create_community_services(factory, resources)
    admin = services.users.create_initial_admin(
        "admin@example.com", "Administrator", "valid password"
    )
    return engine, factory, resources, services, admin


def test_create_dataset_persists_metadata_team_creator_and_audit(dataset_services):
    _, factory, _, services, admin = dataset_services

    dataset = create_owned_dataset(
        services.datasets,
        admin.id,
        "reference_data-1",
        "  Référence data  ",
        "  Preserved Unicode: Straße  ",
    )

    assert uuid.UUID(dataset.id)
    assert dataset.key == "reference_data-1"
    assert dataset.name == "Référence data"
    assert dataset.description == "  Preserved Unicode: Straße  "
    assert dataset.owner_user_id == admin.id
    assert dataset.created_at is not None
    assert dataset.updated_at is not None
    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        grant = session.scalar(
            select(Grant).where(
                Grant.subject_type == "user",
                Grant.subject_id == admin.id,
                Grant.resource_type == "dataset",
                Grant.resource_id == dataset.id,
            )
        )
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.object_type == "dataset"))
        )
    assert persisted.key == dataset.key
    assert grant is None
    assert {(event.object_type, event.object_id, event.action) for event in events} == {
        ("dataset", dataset.id, "created"),
        ("dataset", dataset.id, "dataset.concept_created"),
    }
    assert {event.actor_user_id for event in events} == {admin.id}


def test_create_standalone_mapping_table_without_assumed_schema(dataset_services):
    _, factory, _, services, admin = dataset_services
    mapping = create_owned_dataset(
        services.datasets,
        admin.id,
        "sap-salesforce",
        "SAP to Salesforce",
        "Cost-centre crosswalk",
        kind="mapping",
        mapping_mode="standalone",
        source_system=" SAP ECC ",
        target_system=" Salesforce ",
    )

    assert mapping.kind == "mapping"
    assert mapping.mapping_mode == "standalone"
    assert mapping.source_system == "SAP ECC"
    assert mapping.target_system == "Salesforce"
    with factory() as session:
        fields = list(
            session.scalars(
                select(DatasetField)
                .where(DatasetField.dataset_id == mapping.id)
                .order_by(DatasetField.display_position)
            )
        )
        keys = list(
            session.scalars(
                select(DatasetBusinessKeyField.field_key).where(
                    DatasetBusinessKeyField.dataset_id == mapping.id
                )
            )
        )
    assert fields == []
    assert keys == []


def test_standalone_mapping_source_identifier_is_user_defined(dataset_services):
    _, _, _, services, admin = dataset_services
    mapping = create_owned_dataset(
        services.datasets,
        admin.id,
        "currency-map",
        "Currency map",
        "",
        kind="mapping",
        mapping_mode="standalone",
    )

    source = services.dataset_fields.create_field(
        admin.id,
        mapping.key,
        "currency_code",
        "Currency code",
        "string",
        mapping_role="source_identifier",
    )

    assert source.mapping_role == "source_identifier"
    assert source.is_required
    assert source.is_unique
    assert services.dataset_business_keys.get_business_key(admin.id, mapping.key).field_keys == (
        "currency_code",
    )


def test_linked_mapping_requires_visible_reference_source(dataset_services):
    _, _, _, services, admin = dataset_services
    source = create_owned_dataset(services.datasets, admin.id, "source", "Source", "")
    source_field = services.dataset_fields.create_field(
        admin.id, source.key, "source_code", "Source code", "string"
    )
    mapping = create_owned_dataset(
        services.datasets,
        admin.id,
        "linked",
        "Linked",
        "",
        kind="mapping",
        mapping_mode="linked",
        source_dataset_id=source.id,
        source_field_key=source_field.key,
        target_system="New output code",
    )
    assert mapping.source_dataset_id == source.id
    assert mapping.source_field_key == source_field.key
    assert mapping.source_system == source_field.name
    assert mapping.target_dataset_id is None
    assert mapping.target_field_key is None
    assert mapping.target_system is None
    with pytest.raises(ValidationError, match="source table"):
        create_owned_dataset(
            services.datasets,
            admin.id,
            "missing-source",
            "Missing source",
            "",
            kind="mapping",
            mapping_mode="linked",
        )


def test_linked_mapping_records_source_field_and_defers_target_table(dataset_services):
    _, _, _, services, admin = dataset_services
    source = create_owned_dataset(services.datasets, admin.id, "input", "Input", "")
    target = create_owned_dataset(services.datasets, admin.id, "output", "Output", "")
    source_field = services.dataset_fields.create_field(
        admin.id, source.key, "legacy_code", "Legacy code", "string"
    )
    target_field = services.dataset_fields.create_field(
        admin.id, target.key, "canonical_code", "Canonical code", "string"
    )

    mapping = create_owned_dataset(
        services.datasets,
        admin.id,
        "linked-fields",
        "Linked fields",
        "",
        kind="mapping",
        mapping_mode="linked",
        source_dataset_id=source.id,
        source_field_key=source_field.key,
        target_dataset_id=target.id,
        target_field_key=target_field.key,
    )

    assert (mapping.source_dataset_id, mapping.source_field_key, mapping.source_system) == (
        source.id,
        source_field.key,
        source_field.name,
    )
    assert (mapping.target_dataset_id, mapping.target_field_key, mapping.target_system) == (
        None,
        None,
        None,
    )
    with pytest.raises(ValidationError, match="linked mapping table"):
        services.dataset_fields.delete_field(admin.id, source.key, source_field.key)
    with pytest.raises(ValidationError, match="source identifier"):
        create_owned_dataset(
            services.datasets,
            admin.id,
            "missing-source-field",
            "Missing source field",
            "",
            kind="mapping",
            mapping_mode="linked",
            source_dataset_id=source.id,
            source_field_key="missing",
        )


def test_linked_mapping_populates_source_rows_and_adds_context_and_target_fields(
    dataset_services,
):
    _, _, _, services, admin = dataset_services
    source = create_owned_dataset(services.datasets, admin.id, "currencies", "Currencies", "")
    code = services.dataset_fields.create_field(
        admin.id,
        source.key,
        "code",
        "ISO code",
        "string",
        is_required=True,
        is_business_key_component=True,
    )
    name = services.dataset_fields.create_field(
        admin.id, source.key, "currency_name", "Currency name", "string"
    )
    services.dataset_records.create_record(
        admin.id, source.key, {code.key: "EUR", name.key: "Euro"}
    )
    mapping = create_owned_dataset(
        services.datasets,
        admin.id,
        "tradable-currencies",
        "Tradable currencies",
        "",
        kind="mapping",
        mapping_mode="linked",
        source_dataset_id=source.id,
        source_field_key=code.key,
    )

    with pytest.raises(ValidationError, match="populated from the source table"):
        services.dataset_records.create_record(admin.id, mapping.key, {code.key: "USD"})

    fields = services.dataset_fields.list_fields(admin.id, mapping.key)
    assert [(field.key, field.mapping_role) for field in fields] == [("code", "source_identifier")]
    record = services.dataset_records.list_records(admin.id, mapping.key).records[0]
    assert {value.field_key: value.value for value in record.values} == {"code": "EUR"}

    context = services.dataset_fields.create_field(
        admin.id,
        mapping.key,
        "ignored",
        "Ignored",
        "string",
        mapping_role="source_context",
        source_field_key=name.key,
    )
    target = services.dataset_fields.create_field(
        admin.id,
        mapping.key,
        "tradable",
        "Tradable",
        "boolean",
        mapping_role="mapping_target",
    )
    assert (context.key, context.name, context.mapping_role) == (
        "currency_name",
        "Currency name",
        "source_context",
    )
    assert target.mapping_role == "mapping_target"
    record = services.dataset_records.list_records(admin.id, mapping.key).records[0]
    assert {value.field_key: value.value for value in record.values} == {
        "code": "EUR",
        "currency_name": "Euro",
    }

    with pytest.raises(ValidationError, match="read-only"):
        services.dataset_fields.update_field(
            admin.id, mapping.key, context.key, "Changed", "string", False, False
        )
    updated = services.dataset_records.update_record(
        admin.id,
        mapping.key,
        record.id,
        record.revision,
        {"tradable": "true"},
    )
    assert {value.field_key: value.value for value in updated.values} == {
        "code": "EUR",
        "currency_name": "Euro",
        "tradable": True,
    }


@pytest.mark.parametrize(
    ("kind", "mode"),
    [("unknown", None), ("mapping", None), ("mapping", "many-to-many")],
)
def test_invalid_mapping_type_configuration_is_rejected(dataset_services, kind, mode):
    _, _, _, services, admin = dataset_services
    with pytest.raises(ValidationError):
        create_owned_dataset(
            services.datasets,
            admin.id,
            f"invalid-{kind}-{mode}",
            "Invalid",
            "",
            kind=kind,
            mapping_mode=mode,
        )


@pytest.mark.parametrize("key", ["a", "a" * 100, "a_b-c9"])
def test_valid_dataset_key_boundaries(dataset_services, key):
    _, _, _, services, admin = dataset_services
    assert create_owned_dataset(services.datasets, admin.id, key, "Name", "").key == key


@pytest.mark.parametrize(
    "key",
    ["", "a" * 101, "Upper", "has space", "é", "a.b", "-starts", "_starts", None],
)
def test_noncanonical_dataset_keys_are_rejected_without_rewriting(dataset_services, key):
    _, factory, _, services, admin = dataset_services
    with pytest.raises(ValidationError):
        create_owned_dataset(services.datasets, admin.id, key, "Name", "")
    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 0


def test_dataset_name_is_trimmed_and_nfc_normalized(dataset_services):
    _, _, _, services, admin = dataset_services
    decomposed = "  Cafe\N{COMBINING ACUTE ACCENT}  "
    dataset = create_owned_dataset(services.datasets, admin.id, "cafe", decomposed, "")
    assert dataset.name == unicodedata.normalize("NFC", decomposed.strip())


@pytest.mark.parametrize("name", ["", "   ", "a" * 201, None])
def test_invalid_dataset_names_are_rejected(dataset_services, name):
    _, _, _, services, admin = dataset_services
    with pytest.raises(ValidationError):
        create_owned_dataset(services.datasets, admin.id, "valid", name, "")


def test_dataset_name_maximum_and_description_boundaries(dataset_services):
    _, _, _, services, admin = dataset_services
    first = create_owned_dataset(services.datasets, admin.id, "first", "n" * 200, "")
    description = " \nUnicode Ω" + "x" * (10_000 - len(" \nUnicode Ω"))
    second = create_owned_dataset(services.datasets, admin.id, "second", "Name", description)
    assert len(first.name) == 200
    assert first.description == ""
    assert second.description == description
    with pytest.raises(ValidationError):
        create_owned_dataset(services.datasets, admin.id, "third", "Name", "x" * 10_001)
    with pytest.raises(ValidationError):
        create_owned_dataset(services.datasets, admin.id, "fourth", "Name", None)


def test_creator_cannot_be_supplied_and_key_has_no_public_setter(dataset_services):
    _, _, _, services, admin = dataset_services
    with pytest.raises(TypeError):
        create_owned_dataset(
            services.datasets, admin.id, "dataset", "Name", "", owner_user_id=str(uuid.uuid4())
        )
    dataset = create_owned_dataset(services.datasets, admin.id, "dataset", "Name", "")
    with pytest.raises(AttributeError):
        dataset.key = "replacement"


def test_creation_requires_active_user_and_eligible_team(dataset_services):
    _, factory, _, services, admin = dataset_services
    user = services.users.create_user(admin.id, "user@example.com", "User", "valid password")
    inactive = services.users.create_user(
        admin.id, "inactive@example.com", "Inactive", "valid password", role="admin"
    )
    deleted = services.users.create_user(
        admin.id, "deleted@example.com", "Deleted", "valid password", role="admin"
    )
    with factory.begin() as session:
        session.execute(update(User).where(User.id == inactive.id).values(is_active=False))
        session.execute(
            update(User).where(User.id == deleted.id).values(is_active=False, is_deleted=True)
        )

    with pytest.raises(ValidationError):
        create_owned_dataset(services.datasets, user.id, "no-membership", "Name", "")
    for actor_id in (inactive.id, deleted.id, str(uuid.uuid4())):
        with pytest.raises(AuthorizationError):
            create_owned_dataset(services.datasets, actor_id, f"d-{actor_id[:8]}", "Name", "")
    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 0


def test_duplicate_key_has_consistent_conflict_without_partial_rows(dataset_services):
    _, factory, _, services, admin = dataset_services
    first = create_owned_dataset(services.datasets, admin.id, "duplicate", "First", "")

    with pytest.raises(ConflictError, match="Dataset key already exists in this installation"):
        create_owned_dataset(services.datasets, admin.id, "duplicate", "Second", "")

    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 1
        assert (
            session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
            == 0
        )
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    (AuditEvent.object_type == "dataset")
                    | ((AuditEvent.object_type == "grant") & (AuditEvent.action == "created"))
                )
            )
            == 2
        )
        assert session.scalar(select(Dataset.id)) == first.id


def test_unrelated_dataset_integrity_failure_is_not_reported_as_duplicate(
    dataset_services, monkeypatch
):
    _, factory, _, services, admin = dataset_services
    create = SqlAlchemyDatasetOperations.create

    def create_with_missing_owner(self, **values):
        values["owner_user_id"] = str(uuid.uuid4())
        return create(self, **values)

    monkeypatch.setattr(SqlAlchemyDatasetOperations, "create", create_with_missing_owner)
    with pytest.raises(PersistenceConflictError) as raised:
        create_owned_dataset(services.datasets, admin.id, "not-duplicate", "Name", "")
    assert raised.value.constraint is None
    assert str(raised.value) == "A persistence constraint rejected the operation"
    assert_dataset_creation_tables_empty(factory)


def test_creation_does_not_call_legacy_grant_persistence(dataset_services, monkeypatch):
    _, factory, _, services, admin = dataset_services

    def fail_grant(_self, **_values):
        raise RuntimeError("injected grant failure")

    monkeypatch.setattr(SqlAlchemyGrantOperations, "create", fail_grant)
    dataset = create_owned_dataset(services.datasets, admin.id, "no-grant", "Name", "")
    assert dataset.owner_team_id
    with factory() as session:
        assert session.scalar(select(func.count(Grant.id))) == 0


def test_audit_failure_rolls_back_dataset_creation(dataset_services, monkeypatch):
    _, factory, _, services, admin = dataset_services
    calls = 0

    def fail_audit(_self, **_values):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        create_owned_dataset(services.datasets, admin.id, "rollback", "Name", "")
    assert_dataset_creation_tables_empty(factory)


def assert_dataset_creation_tables_empty(factory):
    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 0
        assert (
            session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
            == 0
        )
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset")
            )
            == 0
        )


def test_dataset_resource_resolver_and_grants_use_dataset_id(dataset_services):
    _, _, resources, services, admin = dataset_services
    dataset = create_owned_dataset(services.datasets, admin.id, "resource", "Name", "")
    user = services.users.create_user(admin.id, "viewer@example.com", "Viewer", "valid password")

    resources.require_existing("dataset", dataset.id)
    with pytest.raises(NotFoundError):
        resources.require_existing("dataset", str(uuid.uuid4()))
    grant = services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, "viewer")
    assert grant.resource_id == dataset.id
    assert services.authorization.effective_permission(user.id, "dataset", dataset.id) is None


def test_owner_metadata_does_not_create_effective_permission(dataset_services):
    _, factory, _, services, admin = dataset_services
    dataset = create_owned_dataset(services.datasets, admin.id, "owned", "Name", "")
    user = services.users.create_user(admin.id, "owner@example.com", "Owner", "valid password")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=user.id)
        )

    assert services.authorization.effective_permission(user.id, "dataset", dataset.id) is None
    assert services.authorization.check_access(user.id, "dataset", dataset.id, "viewer")


def test_persisted_dataset_key_cannot_be_changed(dataset_services):
    _, factory, _, services, admin = dataset_services
    dataset = create_owned_dataset(services.datasets, admin.id, "immutable", "Name", "")

    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        persisted._key = "replacement"
        with pytest.raises(ImmutableDatasetKeyError, match="Dataset key is immutable"):
            session.flush()
        session.rollback()

    with factory() as session:
        assert session.get(Dataset, dataset.id).key == "immutable"


def test_sqlite_concurrent_duplicate_creation_creates_exactly_one(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'datasets.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    users = services.users
    datasets = services.datasets
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return create_owned_dataset(datasets, admin.id, "raced", "Name", "")
        except ConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create(), range(2)))
    assert sum(isinstance(result, Dataset) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    conflict = next(result for result in results if isinstance(result, ConflictError))
    assert str(conflict) == "Dataset key already exists in this installation"
    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 1
        assert (
            session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
            == 0
        )


def test_postgresql_dataset_creation_uniqueness_and_rollback(monkeypatch):
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_database_engine(database_url)
    schema = f"dataset_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    application_engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(application_engine)
        factory = create_session_factory(application_engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        users = services.users
        datasets = services.datasets
        users.bootstrap_organisation()
        admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
        barrier = Barrier(2)

        def create():
            barrier.wait()
            try:
                return create_owned_dataset(datasets, admin.id, "raced", "Name", "")
            except ConflictError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: create(), range(2)))
        assert sum(isinstance(result, Dataset) for result in results) == 1
        assert sum(isinstance(result, ConflictError) for result in results) == 1
        conflict = next(result for result in results if isinstance(result, ConflictError))
        assert str(conflict) == "Dataset key already exists in this installation"

        with monkeypatch.context() as scoped:
            scoped.setattr(
                SqlAlchemyUnitOfWork,
                "add_audit",
                lambda _self, **_values: (_ for _ in ()).throw(RuntimeError("grant failure")),
            )
            with pytest.raises(RuntimeError, match="grant failure"):
                create_owned_dataset(datasets, admin.id, "rollback", "Name", "")
        with factory() as session:
            assert session.scalar(select(func.count(Dataset.id))) == 1
            assert (
                session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
                == 0
            )
    finally:
        application_engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration_engine.dispose()


def test_postgresql_actor_deactivation_serializes_with_dataset_creation():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_database_engine(database_url)
    schema = f"dataset_actor_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    application_engine = create_database_engine(schema_url)
    deactivation_flushed = Event()
    creation_lock_attempted = Event()
    release_deactivation = Event()

    @event.listens_for(application_engine, "before_cursor_execute")
    def observe_locked_actor_read(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if deactivation_flushed.is_set() and "users" in statement and "FOR UPDATE" in statement:
            creation_lock_attempted.set()

    try:
        Base.metadata.create_all(application_engine)
        factory = create_session_factory(application_engine)
        uow = SqlAlchemyUnitOfWorkFactory(factory)
        services = create_community_services(factory, ResourceTypeRegistry())
        users = services.users
        datasets = services.datasets
        users.bootstrap_organisation()
        actor = users.create_initial_admin("actor@example.com", "Actor", "valid password")

        def deactivate_actor():
            with uow(write=True) as work:
                locked_actor = work.users.get(actor.id, for_update=True)
                locked_actor.is_active = False
                work.flush()
                deactivation_flushed.set()
                assert release_deactivation.wait(timeout=5)

        def create_dataset():
            assert deactivation_flushed.wait(timeout=5)
            return create_owned_dataset(datasets, actor.id, "after-revocation", "Name", "")

        with ThreadPoolExecutor(max_workers=2) as executor:
            deactivation = executor.submit(deactivate_actor)
            creation = executor.submit(create_dataset)
            assert creation_lock_attempted.wait(timeout=5)
            assert not creation.done()
            release_deactivation.set()
            deactivation.result(timeout=5)
            with pytest.raises(AuthorizationError):
                creation.result(timeout=5)

        with factory() as session:
            assert session.get(User, actor.id).is_active is False
            assert session.scalar(select(func.count(Dataset.id))) == 0
            assert (
                session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
                == 0
            )
            assert (
                session.scalar(
                    select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset")
                )
                == 0
            )
    finally:
        release_deactivation.set()
        application_engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration_engine.dispose()
