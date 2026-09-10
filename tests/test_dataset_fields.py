import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from dataset_helpers import create_owned_dataset
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import make_url

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import (
    AuditEvent,
    Dataset,
    DatasetField,
    Grant,
    ImmutableDatasetFieldKeyError,
    utc_now,
)
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import (
    ConflictError,
    NotFoundError,
    ResourceTypeRegistry,
    StaleFieldOrderError,
    ValidationError,
)

PASSWORD = "correct horse battery staple"


@pytest.fixture
def field_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Administrator", PASSWORD)
    return engine, factory, services, admin


def create_dataset(services, admin, key="dataset"):
    return create_owned_dataset(services.datasets, admin.id, key, "Dataset", "")


@pytest.mark.parametrize("data_type", ["string", "number", "boolean", "date"])
def test_create_supported_field_types_with_stable_identity_and_defaults(field_services, data_type):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)

    field = services.dataset_fields.create_field(
        admin.id, dataset.key, data_type, data_type.title(), data_type
    )

    assert uuid.UUID(field.id).version == 4
    assert field.dataset_id == dataset.id
    assert field.key == data_type
    assert field.is_required is False
    assert field.is_unique is False


def test_sqlite_database_defaults_round_trip_as_false(field_services):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    field_id = str(uuid.uuid4())
    now = utc_now()
    with factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO dataset_fields "
                "(id, dataset_id, key, name, data_type, display_position, created_at, updated_at) "
                "VALUES (:id, :dataset_id, :key, :name, :data_type, 1, :created_at, :updated_at)"
            ),
            {
                "id": field_id,
                "dataset_id": dataset.id,
                "key": "database_default",
                "name": "Database default",
                "data_type": "string",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )

    with factory() as session:
        field = session.get(DatasetField, field_id)
        assert field.is_required is False
        assert field.is_unique is False


def test_list_fields_is_empty_then_preserves_creation_display_order(field_services):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    assert services.dataset_fields.list_fields(admin.id, dataset.key) == []

    for key in ("zeta", "alpha", "middle"):
        services.dataset_fields.create_field(admin.id, dataset.key, key, key, "string")

    assert [field.key for field in services.dataset_fields.list_fields(admin.id, dataset.key)] == [
        "zeta",
        "alpha",
        "middle",
    ]


def test_reorder_exact_permutation_and_stale_detection(field_services):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    fields = [
        services.dataset_fields.create_field(admin.id, dataset.key, key, key, "string")
        for key in ("first", "second", "third")
    ]
    expected = tuple(field.id for field in fields)
    services.dataset_fields.replace_order(
        admin.id, dataset.key, expected, tuple(reversed(expected))
    )
    assert [field.key for field in services.dataset_fields.list_fields(admin.id, dataset.key)] == [
        "third",
        "second",
        "first",
    ]
    with pytest.raises(StaleFieldOrderError, match="changed since"):
        services.dataset_fields.replace_order(admin.id, dataset.key, expected, expected)


@pytest.mark.parametrize(
    "proposed",
    [
        lambda ids: (ids[0], ids[0], ids[2]),
        lambda ids: ids[:-1],
        lambda ids: (*ids, str(uuid.uuid4())),
    ],
)
def test_reorder_rejects_non_exact_permutations_without_audit(field_services, proposed):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    fields = [
        services.dataset_fields.create_field(admin.id, dataset.key, key, key, "string")
        for key in ("first", "second", "third")
    ]
    expected = tuple(field.id for field in fields)
    with pytest.raises(ValidationError):
        services.dataset_fields.replace_order(admin.id, dataset.key, expected, proposed(expected))
    assert tuple(
        field.id for field in services.dataset_fields.list_fields(admin.id, dataset.key)
    ) == (expected)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "field_order_updated")
            )
            == 0
        )


def test_reorder_audit_failure_rolls_back_complete_order(field_services, monkeypatch):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    fields = [
        services.dataset_fields.create_field(admin.id, dataset.key, key, key, "string")
        for key in ("first", "second")
    ]
    expected = tuple(field.id for field in fields)
    original_add_audit = SqlAlchemyUnitOfWork.add_audit

    def fail_order_audit(self, **values):
        if values["action"] == "field_order_updated":
            raise RuntimeError("injected order audit failure")
        return original_add_audit(self, **values)

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_order_audit)
    with pytest.raises(RuntimeError, match="injected order audit failure"):
        services.dataset_fields.replace_order(
            admin.id, dataset.key, expected, tuple(reversed(expected))
        )
    assert tuple(
        field.id for field in services.dataset_fields.list_fields(admin.id, dataset.key)
    ) == (expected)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "field_order_updated")
            )
            == 0
        )


def test_delete_compacts_field_positions(field_services):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    for key in ("first", "second", "third"):
        services.dataset_fields.create_field(admin.id, dataset.key, key, key, "string")
    services.dataset_fields.delete_field(admin.id, dataset.key, "second")
    fields = services.dataset_fields.list_fields(admin.id, dataset.key)
    assert [(field.key, field.display_position) for field in fields] == [("first", 1), ("third", 2)]


@pytest.mark.parametrize("key", ["a", "a" * 100, "a0_b"])
def test_field_key_valid_boundaries(field_services, key):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    assert (
        services.dataset_fields.create_field(admin.id, dataset.key, key, "Name", "string").key
        == key
    )


@pytest.mark.parametrize(
    "key", ["", "a" * 101, "Upper", "white space", "hyphen-key", "é", "_leading"]
)
def test_field_key_rejects_noncanonical_values(field_services, key):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    with pytest.raises(ValidationError, match="Field key"):
        services.dataset_fields.create_field(admin.id, dataset.key, key, "Name", "string")


def test_field_key_scope_and_duplicate_conflict(field_services):
    _, _, services, admin = field_services
    first = create_dataset(services, admin, "first")
    second = create_dataset(services, admin, "second")
    first_field = services.dataset_fields.create_field(
        admin.id, first.key, "shared", "First", "string"
    )
    second_field = services.dataset_fields.create_field(
        admin.id, second.key, "shared", "Second", "number"
    )

    assert first_field.dataset_id != second_field.dataset_id
    assert services.dataset_fields.get_field(admin.id, first.key, "shared").id == first_field.id
    assert services.dataset_fields.get_field(admin.id, second.key, "shared").id == second_field.id
    with pytest.raises(ConflictError, match="Field key already exists in this Dataset"):
        services.dataset_fields.create_field(admin.id, first.key, "shared", "Duplicate", "boolean")


def test_persisted_field_key_is_immutable(field_services):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    field = services.dataset_fields.create_field(
        admin.id, dataset.key, "immutable", "Name", "string"
    )

    with factory() as session:
        persisted = session.get(DatasetField, field.id)
        persisted._key = "replacement"
        with pytest.raises(ImmutableDatasetFieldKeyError, match="key is immutable"):
            session.flush()
        session.rollback()


@pytest.mark.parametrize(
    ("submitted", "expected"),
    [
        (" N\N{COMBINING TILDE}ame ", "Ñame"),
        ("x", "x"),
        ("x" * 200, "x" * 200),
    ],
)
def test_field_name_trim_normalization_and_boundaries(field_services, submitted, expected):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    field = services.dataset_fields.create_field(
        admin.id, dataset.key, "field", submitted, "string"
    )
    assert field.name == expected


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_field_name_rejects_invalid_lengths(field_services, name):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    with pytest.raises(ValidationError, match="Field name"):
        services.dataset_fields.create_field(admin.id, dataset.key, "field", name, "string")


@pytest.mark.parametrize("data_type", ["decimal", "uuid", "String", ""])
def test_unsupported_field_type_is_rejected(field_services, data_type):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    with pytest.raises(ValidationError, match="Field type"):
        services.dataset_fields.create_field(admin.id, dataset.key, "field", "Name", data_type)


@pytest.mark.parametrize(
    ("is_required", "is_unique"), [(1, False), (False, 0), ("true", False), (False, None)]
)
def test_non_boolean_flags_are_rejected(field_services, is_required, is_unique):
    _, _, services, admin = field_services
    dataset = create_dataset(services, admin)
    with pytest.raises(ValidationError, match="must be a boolean"):
        services.dataset_fields.create_field(
            admin.id,
            dataset.key,
            "field",
            "Name",
            "string",
            is_required,
            is_unique,
        )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"name": "Changed"}, ("Changed", "string", False, False)),
        ({"data_type": "date"}, ("Name", "date", False, False)),
        ({"is_required": True}, ("Name", "string", True, False)),
        ({"is_unique": True}, ("Name", "string", False, True)),
    ],
)
def test_update_each_mutable_attribute(field_services, changes, expected):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    field = services.dataset_fields.create_field(admin.id, dataset.key, "field", "Name", "string")
    values = {
        "name": "Name",
        "data_type": "string",
        "is_required": False,
        "is_unique": False,
        **changes,
    }

    updated = services.dataset_fields.update_field(admin.id, dataset.key, field.key, **values)

    assert (updated.name, updated.data_type, updated.is_required, updated.is_unique) == expected
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "definition_updated",
                )
            )
            == 1
        )


def test_normalized_noop_does_not_write_timestamp_or_audit(field_services):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    field = services.dataset_fields.create_field(admin.id, dataset.key, "field", "Ñame", "string")
    with factory() as session:
        original_updated_at = session.get(DatasetField, field.id).updated_at

    returned = services.dataset_fields.update_field(
        admin.id,
        dataset.key,
        field.key,
        " N\N{COMBINING TILDE}ame ",
        "string",
        False,
        False,
    )

    assert returned.updated_at == original_updated_at
    with factory() as session:
        persisted = session.get(DatasetField, field.id)
        assert persisted.updated_at == original_updated_at
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "definition_updated",
                )
            )
            == 0
        )


def test_create_and_update_emit_expected_audits(field_services):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    field = services.dataset_fields.create_field(admin.id, dataset.key, "field", "Name", "string")
    services.dataset_fields.update_field(
        admin.id, dataset.key, field.key, "Changed", "number", True, True
    )

    with factory() as session:
        assert list(
            session.scalars(
                select(AuditEvent.action)
                .where(
                    AuditEvent.object_type == "dataset_field",
                    AuditEvent.object_id == field.id,
                )
                .order_by(AuditEvent.timestamp)
            )
        ) == ["created", "definition_updated"]


@pytest.mark.parametrize("permission", ["viewer", "editor"])
def test_viewer_and_editor_can_read_but_cannot_mutate(field_services, permission):
    _, _, services, admin = field_services
    user = services.users.create_user(
        admin.id, f"{permission}@example.com", permission.title(), PASSWORD
    )
    dataset = create_dataset(services, admin)
    field = services.dataset_fields.create_field(admin.id, dataset.key, "field", "Name", "string")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)

    assert services.dataset_fields.list_fields(user.id, dataset.key)[0].id == field.id
    assert services.dataset_fields.get_field(user.id, dataset.key, field.key).id == field.id
    with pytest.raises(NotFoundError, match="Dataset not found"):
        services.dataset_fields.create_field(user.id, dataset.key, "blocked", "Blocked", "string")
    with pytest.raises(NotFoundError, match="Dataset not found"):
        services.dataset_fields.update_field(
            user.id, dataset.key, field.key, "Blocked", "string", False, False
        )


def test_owning_member_can_mutate_schema_with_inert_legacy_grant(field_services):
    _, _, services, admin = field_services
    user = services.users.create_user(admin.id, "schema-admin@example.com", "Admin", PASSWORD)
    dataset = create_dataset(services, admin)
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, "admin")

    services.memberships.create_membership(admin.id, user.id, dataset.owner_team_id)
    field = services.dataset_fields.create_field(user.id, dataset.key, "field", "Name", "string")
    assert (
        services.dataset_fields.update_field(
            user.id, dataset.key, field.key, "Changed", "boolean", True, True
        ).data_type
        == "boolean"
    )


def test_community_admin_override_does_not_depend_on_dataset_grant(field_services):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    with factory.begin() as session:
        session.execute(
            delete(Grant).where(Grant.resource_type == "dataset", Grant.resource_id == dataset.id)
        )

    field = services.dataset_fields.create_field(admin.id, dataset.key, "field", "Name", "string")
    assert services.dataset_fields.list_fields(admin.id, dataset.key)[0].id == field.id


def test_owner_metadata_does_not_authorize_field_access(field_services):
    _, factory, services, admin = field_services
    owner = services.users.create_user(admin.id, "owner@example.com", "Owner", PASSWORD)
    dataset = create_dataset(services, admin)
    services.dataset_fields.create_field(admin.id, dataset.key, "field", "Name", "string")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=owner.id)
        )

    assert len(services.dataset_fields.list_fields(owner.id, dataset.key)) == 1
    with pytest.raises(NotFoundError, match="Dataset not found"):
        services.dataset_fields.create_field(owner.id, dataset.key, "blocked", "Blocked", "string")
    with pytest.raises(NotFoundError, match="Dataset not found"):
        services.dataset_fields.update_field(
            owner.id, dataset.key, "_malformed", "Blocked", "string", False, False
        )


def test_create_audit_failure_rolls_back_field(field_services, monkeypatch):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)

    def fail_audit(_self, **_values):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        services.dataset_fields.create_field(admin.id, dataset.key, "field", "Name", "string")
    with factory() as session:
        assert session.scalar(select(func.count(DatasetField.id))) == 0
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset_field")
            )
            == 0
        )


def test_create_identifier_field_audit_failure_rolls_back_field_and_membership(
    field_services, monkeypatch
):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    original = SqlAlchemyUnitOfWork.add_audit

    def fail_identifier_audit(self, **values):
        if values["object_type"] == "dataset":
            raise RuntimeError("injected identifier audit failure")
        original(self, **values)

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_identifier_audit)
    with pytest.raises(RuntimeError, match="injected identifier audit failure"):
        services.dataset_fields.create_field(
            admin.id,
            dataset.key,
            "field",
            "Name",
            "string",
            is_required=True,
            is_business_key_component=True,
        )
    with factory() as session:
        assert session.scalar(select(func.count(DatasetField.id))) == 0
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset_field")
            )
            == 0
        )
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == ()


def test_update_audit_failure_rolls_back_definition(field_services, monkeypatch):
    _, factory, services, admin = field_services
    dataset = create_dataset(services, admin)
    field = services.dataset_fields.create_field(
        admin.id, dataset.key, "field", "Original", "string"
    )

    def fail_audit(_self, **_values):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        services.dataset_fields.update_field(
            admin.id, dataset.key, field.key, "Changed", "number", True, True
        )
    with factory() as session:
        persisted = session.get(DatasetField, field.id)
        assert (persisted.name, persisted.data_type) == ("Original", "string")
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "definition_updated",
                )
            )
            == 0
        )


def test_sqlite_concurrent_duplicate_field_creation_creates_exactly_one(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'fields.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    dataset = create_dataset(services, admin)
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return services.dataset_fields.create_field(
                admin.id, dataset.key, "raced", "Name", "string"
            )
        except ConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create(), range(2)))

    assert sum(isinstance(result, DatasetField) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count(DatasetField.id))) == 1
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset_field")
            )
            == 1
        )


def test_postgresql_field_creation_duplicate_rollback_and_race(monkeypatch):
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_database_engine(database_url)
    schema = f"dataset_fields_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        services.users.bootstrap_organisation()
        admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
        dataset = create_dataset(services, admin)
        services.dataset_fields.create_field(admin.id, dataset.key, "duplicate", "Name", "string")
        with pytest.raises(ConflictError, match="Field key already exists in this Dataset"):
            services.dataset_fields.create_field(
                admin.id, dataset.key, "duplicate", "Name", "string"
            )

        default_field_id = str(uuid.uuid4())
        now = utc_now()
        with factory.begin() as session:
            session.execute(
                text(
                    "INSERT INTO dataset_fields "
                    "(id, dataset_id, key, name, data_type, display_position, "
                    "created_at, updated_at) "
                    "VALUES "
                    "(:id, :dataset_id, :key, :name, :data_type, 2, :created_at, :updated_at)"
                ),
                {
                    "id": default_field_id,
                    "dataset_id": dataset.id,
                    "key": "database_default",
                    "name": "Database default",
                    "data_type": "string",
                    "created_at": now,
                    "updated_at": now,
                },
            )
        with factory() as session:
            default_field = session.get(DatasetField, default_field_id)
            assert default_field.is_required is False
            assert default_field.is_unique is False

        with monkeypatch.context() as scoped:
            scoped.setattr(
                SqlAlchemyUnitOfWork,
                "add_audit",
                lambda _self, **_values: (_ for _ in ()).throw(
                    RuntimeError("injected audit failure")
                ),
            )
            with pytest.raises(RuntimeError, match="injected audit failure"):
                services.dataset_fields.create_field(
                    admin.id, dataset.key, "rollback", "Name", "string"
                )

        update_target = services.dataset_fields.create_field(
            admin.id, dataset.key, "update_rollback", "Original", "string"
        )
        with monkeypatch.context() as scoped:
            scoped.setattr(
                SqlAlchemyUnitOfWork,
                "add_audit",
                lambda _self, **_values: (_ for _ in ()).throw(
                    RuntimeError("injected update audit failure")
                ),
            )
            with pytest.raises(RuntimeError, match="injected update audit failure"):
                services.dataset_fields.update_field(
                    admin.id,
                    dataset.key,
                    update_target.key,
                    "Changed",
                    "number",
                    True,
                    True,
                )

        barrier = Barrier(2)

        def create_raced():
            barrier.wait()
            try:
                return services.dataset_fields.create_field(
                    admin.id, dataset.key, "raced", "Name", "string"
                )
            except ConflictError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: create_raced(), range(2)))
        assert sum(isinstance(result, DatasetField) for result in results) == 1
        assert sum(isinstance(result, ConflictError) for result in results) == 1
        with factory() as session:
            assert (
                session.scalar(
                    select(func.count(DatasetField.id)).where(DatasetField._key == "rollback")
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count(DatasetField.id)).where(DatasetField._key == "raced")
                )
                == 1
            )
            persisted_update = session.get(DatasetField, update_target.id)
            assert (
                persisted_update.name,
                persisted_update.data_type,
                persisted_update.is_required,
                persisted_update.is_unique,
            ) == ("Original", "string", False, False)
            assert (
                session.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.object_id == update_target.id,
                        AuditEvent.action == "definition_updated",
                    )
                )
                == 0
            )
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        administration_engine.dispose()
