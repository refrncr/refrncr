import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event, local

import pytest
from alembic import command
from alembic.config import Config
from dataset_helpers import create_owned_dataset, downgrade_historical_fixture, publish_dataset
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Dataset, DatasetBusinessKeyField, DatasetField, Grant
from refrncr.persistence import SqlAlchemyDatasetOperations, SqlAlchemyUnitOfWork
from refrncr.services import NotFoundError, ResourceTypeRegistry, ValidationError

PASSWORD = "correct horse battery staple"


@pytest.fixture
def business_key_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Administrator", PASSWORD)
    return engine, factory, services, admin


@pytest.fixture
def postgresql_business_key_services():
    import os

    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"business_key_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        config = Config("alembic.ini")
        rendered_url = schema_url.render_as_string(hide_password=False)
        config.set_main_option("sqlalchemy.url", rendered_url.replace("%", "%%"))
        command.upgrade(config, "head")
        factory = create_session_factory(engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        services.users.bootstrap_organisation()
        admin = services.users.create_initial_admin("admin@example.com", "Administrator", PASSWORD)
        yield engine, factory, services, admin, config
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration_engine.dispose()


def create_dataset(services, admin, key="dataset"):
    return create_owned_dataset(services.datasets, admin.id, key, "Dataset", "")


def create_required_field(services, admin, dataset, key, data_type="string", *, unique=False):
    return services.dataset_fields.create_field(
        admin.id,
        dataset.key,
        key,
        key.title(),
        data_type,
        is_required=True,
        is_unique=unique,
    )


def grant(services, admin, user, dataset, permission):
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)


def test_initially_unconfigured_then_single_and_composite_keys_are_canonical(
    business_key_services,
):
    _, _, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    for key in ("zeta", "alpha", "middle"):
        create_required_field(services, admin, dataset, key)

    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == ()
    assert services.dataset_business_keys.set_business_key(
        admin.id, dataset.key, ("middle",)
    ).field_keys == ("middle",)
    assert services.dataset_business_keys.set_business_key(
        admin.id, dataset.key, ("zeta", "alpha")
    ).field_keys == ("alpha", "zeta")


@pytest.mark.parametrize("data_type", ["string", "number", "boolean", "date"])
def test_every_field_type_and_non_unique_components_are_accepted(business_key_services, data_type):
    _, _, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "field", data_type, unique=False)

    result = services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("field",))

    assert result.field_keys == ("field",)


@pytest.mark.parametrize(
    ("field_keys", "message"),
    [
        ((), "At least one"),
        (("field", "field"), "duplicates"),
        (("missing",), "does not exist"),
    ],
)
def test_invalid_component_selections_are_rejected(business_key_services, field_keys, message):
    _, _, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "field")

    with pytest.raises(ValidationError, match=message):
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, field_keys)


def test_non_required_and_cross_dataset_components_are_rejected(business_key_services):
    _, _, services, admin = business_key_services
    first = create_dataset(services, admin, "first")
    second = create_dataset(services, admin, "second")
    services.dataset_fields.create_field(admin.id, first.key, "optional", "Optional", "string")
    create_required_field(services, admin, second, "foreign")

    with pytest.raises(ValidationError, match="must be required"):
        services.dataset_business_keys.set_business_key(admin.id, first.key, ("optional",))
    with pytest.raises(ValidationError, match="does not exist"):
        services.dataset_business_keys.set_business_key(admin.id, first.key, ("foreign",))


def test_same_field_key_is_resolved_only_within_parent_dataset(business_key_services):
    _, factory, services, admin = business_key_services
    first = create_dataset(services, admin, "first")
    second = create_dataset(services, admin, "second")
    create_required_field(services, admin, first, "shared")
    create_required_field(services, admin, second, "shared")

    services.dataset_business_keys.set_business_key(admin.id, first.key, ("shared",))

    with factory() as session:
        rows = list(session.scalars(select(DatasetBusinessKeyField)))
        assert [(row.dataset_id, row.field_key) for row in rows] == [(first.id, "shared")]


def test_canonical_no_op_performs_no_mutation_or_audit(business_key_services):
    engine, factory, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "alpha")
    create_required_field(services, admin, dataset, "zeta")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("alpha", "zeta"))
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "DATASET_BUSINESS_KEY_FIELDS" in normalized:
            statements.append(normalized)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        exact_result = services.dataset_business_keys.set_business_key(
            admin.id, dataset.key, ("alpha", "zeta")
        )
        reordered_result = services.dataset_business_keys.set_business_key(
            admin.id, dataset.key, ("zeta", "alpha")
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert exact_result.field_keys == ("alpha", "zeta")
    assert reordered_result.field_keys == ("alpha", "zeta")
    assert not any(statement.startswith(("DELETE", "INSERT")) for statement in statements)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == dataset.id,
                    AuditEvent.action.in_(("business_key_defined", "business_key_updated")),
                )
            )
            == 1
        )


def test_audit_actions_distinguish_initial_definition_and_replacement(business_key_services):
    _, factory, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "first")
    create_required_field(services, admin, dataset, "second")

    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("first",))
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("second",))

    with factory() as session:
        actions = tuple(
            session.scalars(
                select(AuditEvent.action)
                .where(
                    AuditEvent.object_type == "dataset",
                    AuditEvent.object_id == dataset.id,
                    AuditEvent.action.like("business_key_%"),
                )
                .order_by(AuditEvent.timestamp, AuditEvent.id)
            )
        )
    assert actions == ("business_key_defined", "business_key_updated")


def test_viewer_and_editor_can_read_but_cannot_mutate(business_key_services):
    _, _, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "field")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("field",))
    viewer = services.users.create_user(admin.id, "viewer@example.com", "Viewer", PASSWORD)
    editor = services.users.create_user(admin.id, "editor@example.com", "Editor", PASSWORD)
    grant(services, admin, viewer, dataset, "viewer")
    grant(services, admin, editor, dataset, "editor")

    publish_dataset(services, admin.id, dataset)
    assert services.dataset_business_keys.get_business_key(viewer.id, dataset.key).field_keys == (
        "field",
    )
    assert services.dataset_business_keys.get_business_key(editor.id, dataset.key).field_keys == (
        "field",
    )
    for actor in (viewer, editor):
        with pytest.raises(NotFoundError, match="Dataset not found"):
            services.dataset_business_keys.set_business_key(actor.id, dataset.key, ("field",))


def test_owning_member_and_community_admin_can_mutate(business_key_services):
    _, factory, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "field")
    dataset_admin = services.users.create_user(
        admin.id, "dataset-admin@example.com", "Dataset Admin", PASSWORD
    )
    services.memberships.create_membership(admin.id, dataset_admin.id, dataset.owner_team_id)

    services.dataset_business_keys.set_business_key(dataset_admin.id, dataset.key, ("field",))
    with factory.begin() as session:
        session.query(Grant).filter_by(resource_type="dataset", resource_id=dataset.id).delete()

    create_required_field(services, admin, dataset, "replacement")
    result = services.dataset_business_keys.set_business_key(
        admin.id, dataset.key, ("replacement",)
    )
    assert result.field_keys == ("replacement",)
    with factory() as session:
        persisted = tuple(
            session.scalars(
                select(DatasetBusinessKeyField.field_key).where(
                    DatasetBusinessKeyField.dataset_id == dataset.id
                )
            )
        )
        assert persisted == ("replacement",)
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == dataset.id,
                    AuditEvent.action == "business_key_updated",
                )
            )
            == 1
        )


def test_owner_metadata_does_not_authorize(business_key_services):
    _, factory, services, admin = business_key_services
    owner = services.users.create_user(admin.id, "owner@example.com", "Owner", PASSWORD)
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "field")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=owner.id)
        )

    publish_dataset(services, admin.id, dataset)
    assert services.dataset_business_keys.get_business_key(owner.id, dataset.key).field_keys == (
        "field",
    )
    with pytest.raises(NotFoundError, match="Dataset not found"):
        services.dataset_business_keys.set_business_key(owner.id, dataset.key, ("field",))
    with pytest.raises(NotFoundError, match="Dataset not found"):
        services.dataset_business_keys.set_business_key(owner.id, dataset.key, ())


def test_business_key_component_cannot_become_optional_but_other_changes_are_allowed(
    business_key_services,
):
    _, _, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    component = create_required_field(services, admin, dataset, "component")
    other = create_required_field(services, admin, dataset, "other")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, (component.key,))

    with pytest.raises(ValidationError, match="must remain required"):
        services.dataset_fields.update_field(
            admin.id, dataset.key, component.key, "Changed", "number", False, True
        )
    changed = services.dataset_fields.update_field(
        admin.id, dataset.key, component.key, "Changed", "number", True, True
    )
    optional = services.dataset_fields.update_field(
        admin.id, dataset.key, other.key, other.name, other.data_type, False, other.is_unique
    )
    assert (changed.name, changed.data_type, changed.is_unique) == ("Changed", "number", True)
    assert optional.is_required is False


def test_immediate_audit_failure_rolls_back_initial_and_replacement(
    business_key_services, monkeypatch
):
    _, _, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "first")
    create_required_field(services, admin, dataset, "second")

    def fail_audit(_self, **_values):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("first",))
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == ()

    monkeypatch.undo()
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("first",))
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("second",))
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == (
        "first",
    )


def test_commit_time_audit_failure_restores_complete_previous_key(
    business_key_services, monkeypatch
):
    _, _, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    for key in ("first", "second", "third"):
        create_required_field(services, admin, dataset, key)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("first", "second"))
    original = SqlAlchemyUnitOfWork.add_audit

    def invalid_audit(self, **values):
        original(self, **{**values, "actor_user_id": str(uuid.uuid4())})

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", invalid_audit)
    with pytest.raises(IntegrityError):
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("third",))
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == (
        "first",
        "second",
    )


def test_combined_field_and_identifier_update_rolls_back_together(
    business_key_services, monkeypatch
):
    _, factory, services, admin = business_key_services
    dataset = create_dataset(services, admin)
    field = create_required_field(services, admin, dataset, "field")

    def fail_audit(_self, **_values):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        services.dataset_fields.update_field_and_business_key_membership(
            admin.id,
            dataset.key,
            field.key,
            "Changed",
            "number",
            True,
            True,
            True,
        )
    with factory() as session:
        persisted = session.get(DatasetField, field.id)
        assert (
            persisted.name,
            persisted.data_type,
            persisted.is_required,
            persisted.is_unique,
        ) == (
            field.name,
            field.data_type,
            True,
            False,
        )
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == ()

    monkeypatch.undo()
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, (field.key,))
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        services.dataset_fields.update_field_and_business_key_membership(
            admin.id,
            dataset.key,
            field.key,
            field.name,
            field.data_type,
            False,
            field.is_unique,
            False,
        )
    with factory() as session:
        assert session.get(DatasetField, field.id).is_required is True
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == (
        field.key,
    )


def test_postgresql_fk_and_commit_failure_rollback(postgresql_business_key_services, monkeypatch):
    _, factory, services, admin, _config = postgresql_business_key_services
    first = create_dataset(services, admin, "first")
    second = create_dataset(services, admin, "second")
    create_required_field(services, admin, first, "old")
    foreign = create_required_field(services, admin, second, "foreign")
    create_required_field(services, admin, first, "new")
    services.dataset_business_keys.set_business_key(admin.id, first.key, ("old",))

    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(DatasetBusinessKeyField(dataset_id=first.id, field_key=foreign.key))

    original = SqlAlchemyUnitOfWork.add_audit

    def invalid_audit(self, **values):
        original(self, **{**values, "actor_user_id": str(uuid.uuid4())})

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", invalid_audit)
    with pytest.raises(IntegrityError):
        services.dataset_business_keys.set_business_key(admin.id, first.key, ("new",))
    assert services.dataset_business_keys.get_business_key(admin.id, first.key).field_keys == (
        "old",
    )


def test_postgresql_populated_downgrade_refuses_then_succeeds(
    postgresql_business_key_services,
):
    engine, factory, services, admin, config = postgresql_business_key_services
    dataset = create_dataset(services, admin)
    create_required_field(services, admin, dataset, "field")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("field",))

    engine.dispose()
    with pytest.raises(RuntimeError, match="Dataset business-key rows exist"):
        downgrade_historical_fixture(config, "0006_ref_74")

    with factory.begin() as session:
        session.query(DatasetBusinessKeyField).delete()
    engine.dispose()
    downgrade_historical_fixture(config, "0006_ref_74")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0006_ref_74"


@pytest.mark.parametrize("first_operation", ["configure", "make_optional"])
def test_postgresql_replace_and_optional_update_serialize_in_both_orders(
    postgresql_business_key_services, monkeypatch, first_operation
):
    engine, factory, services, admin, _config = postgresql_business_key_services
    dataset = create_dataset(services, admin)
    field = create_required_field(services, admin, dataset, "field")
    thread_state = local()
    first_has_lock = Event()
    release_first = Event()
    second_lock_attempted = Event()
    second_has_lock = Event()
    original_find_by_key = SqlAlchemyDatasetOperations.find_by_key

    def observed_find_by_key(self, key, *, for_update=False):
        result = original_find_by_key(self, key, for_update=for_update)
        if for_update and getattr(thread_state, "position", None) == "first":
            first_has_lock.set()
            assert release_first.wait(timeout=10), "test did not release the first Dataset lock"
        elif for_update and getattr(thread_state, "position", None) == "second":
            second_has_lock.set()
        return result

    def observe_lock_statement(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            getattr(thread_state, "position", None) == "second"
            and "FOR UPDATE" in statement.upper()
        ):
            second_lock_attempted.set()

    monkeypatch.setattr(SqlAlchemyDatasetOperations, "find_by_key", observed_find_by_key)
    event.listen(engine, "before_cursor_execute", observe_lock_statement)

    def configure_key():
        return services.dataset_business_keys.set_business_key(admin.id, dataset.key, (field.key,))

    def make_optional():
        return services.dataset_fields.update_field(
            admin.id,
            dataset.key,
            field.key,
            field.name,
            field.data_type,
            False,
            field.is_unique,
        )

    operations = {"configure": configure_key, "make_optional": make_optional}
    second_operation = "make_optional" if first_operation == "configure" else "configure"

    def run(position, operation):
        thread_state.position = position
        return operations[operation]()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(run, "first", first_operation)
            try:
                assert first_has_lock.wait(timeout=10), (
                    "first transaction did not acquire Dataset lock"
                )
                second_future = executor.submit(run, "second", second_operation)
                assert second_lock_attempted.wait(timeout=10), (
                    "second transaction did not attempt lock"
                )
                assert not second_has_lock.is_set()
                assert not second_future.done()
            finally:
                release_first.set()

            first_result = first_future.result(timeout=10)
            with pytest.raises(ValidationError) as failure:
                second_future.result(timeout=10)
    finally:
        release_first.set()
        event.remove(engine, "before_cursor_execute", observe_lock_statement)

    assert second_has_lock.is_set()
    if first_operation == "configure":
        assert first_result.field_keys == (field.key,)
        assert str(failure.value) == "A business-key field must remain required"
    else:
        assert first_result.is_required is False
        assert str(failure.value) == "Every business-key field must be required"

    with factory() as session:
        persisted_field = session.scalar(
            select(DatasetField).where(
                DatasetField.dataset_id == dataset.id,
                DatasetField._key == field.key,
            )
        )
        component_exists = session.get(DatasetBusinessKeyField, (dataset.id, field.key)) is not None
        assert not (component_exists and not persisted_field.is_required)
        assert component_exists is (first_operation == "configure")
        assert persisted_field.is_required is (first_operation == "configure")
