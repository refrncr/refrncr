import os
import uuid

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import make_url

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Dataset, User
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import AuthorizationError, NotFoundError, ResourceTypeRegistry


@pytest.fixture
def dataset_access():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    admin = services.users.create_initial_admin(
        "admin@example.com", "Administrator", "valid password"
    )
    user = services.users.create_user(admin.id, "user@example.com", "User", "valid password")
    return engine, factory, services, admin, user


def create_dataset(services, admin, key):
    return services.datasets.create_dataset(admin.id, key, key.title(), f"About {key}")


@pytest.mark.parametrize("permission", ["viewer", "editor", "admin"])
def test_direct_grants_allow_listing_and_read(dataset_access, permission):
    _, _, services, admin, user = dataset_access
    visible = create_dataset(services, admin, "visible")
    create_dataset(services, admin, "hidden")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", visible.id, permission)

    assert [dataset.key for dataset in services.datasets.list_datasets(user.id)] == ["visible"]
    assert services.datasets.get_dataset(user.id, "visible").id == visible.id


@pytest.mark.parametrize("team_permission", ["viewer", "editor", "admin"])
def test_listing_combines_direct_and_current_team_grants_in_key_order(
    dataset_access, team_permission
):
    _, _, services, admin, user = dataset_access
    zulu = create_dataset(services, admin, "zulu")
    alpha = create_dataset(services, admin, "alpha")
    hidden = create_dataset(services, admin, "hidden")
    team = services.teams.create_team(admin.id, "Readers")
    membership = services.memberships.create_membership(admin.id, user.id, team.id)
    services.grants.create_grant(admin.id, "team", team.id, "dataset", zulu.id, team_permission)
    services.grants.create_grant(admin.id, "user", user.id, "dataset", alpha.id, "editor")

    assert [dataset.key for dataset in services.datasets.list_datasets(user.id)] == [
        "alpha",
        "zulu",
    ]

    services.memberships.delete_membership(admin.id, team.id, membership.id)
    assert [dataset.key for dataset in services.datasets.list_datasets(user.id)] == ["alpha"]
    with pytest.raises(NotFoundError, match="^Dataset not found$"):
        services.datasets.get_dataset(user.id, zulu.key)
    assert services.datasets.get_dataset(admin.id, hidden.key).id == hidden.id


def test_community_admin_lists_all_without_resource_grants(dataset_access):
    _, factory, services, admin, _ = dataset_access
    beta = create_dataset(services, admin, "beta")
    alpha = create_dataset(services, admin, "alpha")
    with factory.begin() as session:
        session.execute(text("DELETE FROM grants WHERE resource_type = 'dataset'"))

    assert [dataset.id for dataset in services.datasets.list_datasets(admin.id)] == [
        alpha.id,
        beta.id,
    ]
    assert services.datasets.get_dataset(admin.id, beta.key).id == beta.id


def test_ownership_does_not_authorize_listing_or_read(dataset_access):
    _, factory, services, admin, user = dataset_access
    dataset = create_dataset(services, admin, "owned")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=user.id)
        )

    assert services.datasets.list_datasets(user.id) == []
    with pytest.raises(NotFoundError, match="^Dataset not found$"):
        services.datasets.get_dataset(user.id, dataset.key)
    with pytest.raises(NotFoundError, match="^Dataset not found$"):
        services.datasets.update_dataset(user.id, dataset.key, "Changed", "Changed")


@pytest.mark.parametrize("state", ["missing", "inactive", "deleted"])
def test_ineligible_actor_cannot_list_datasets(dataset_access, state):
    _, factory, services, admin, user = dataset_access
    create_dataset(services, admin, "dataset")
    actor_id = "missing-user"
    if state != "missing":
        actor_id = user.id
        with factory.begin() as session:
            session.execute(
                update(User)
                .where(User.id == user.id)
                .values(is_active=False, is_deleted=state == "deleted")
            )

    with pytest.raises(AuthorizationError, match="^Active user access required$"):
        services.datasets.list_datasets(actor_id)


@pytest.mark.parametrize("state", ["inactive", "deleted"])
def test_ineligible_granted_actor_cannot_read_or_update(dataset_access, state):
    _, factory, services, admin, user = dataset_access
    dataset = create_dataset(services, admin, "protected")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, "admin")
    with factory.begin() as session:
        session.execute(
            update(User)
            .where(User.id == user.id)
            .values(is_active=False, is_deleted=state == "deleted")
        )

    with pytest.raises(NotFoundError, match="^Dataset not found$"):
        services.datasets.get_dataset(user.id, dataset.key)
    with pytest.raises(NotFoundError, match="^Dataset not found$"):
        services.datasets.update_dataset(user.id, dataset.key, "Changed", "Changed")


def test_missing_and_inaccessible_dataset_are_indistinguishable(dataset_access):
    _, _, services, admin, user = dataset_access
    create_dataset(services, admin, "private")

    messages = []
    for key in ("private", "missing", "INVALID KEY"):
        with pytest.raises(NotFoundError) as captured:
            services.datasets.get_dataset(user.id, key)
        messages.append(str(captured.value))

    assert messages == ["Dataset not found"] * 3


def test_listing_uses_bounded_queries_instead_of_per_dataset_authorization(dataset_access):
    engine, _, services, admin, user = dataset_access
    for index in range(6):
        dataset = create_dataset(services, admin, f"dataset-{index}")
        services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, "viewer")
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert len(services.datasets.list_datasets(user.id)) == 6
    finally:
        event.remove(engine, "before_cursor_execute", observe)

    assert len(statements) == 3


@pytest.mark.parametrize("permission", ["editor", "admin"])
def test_editor_or_resource_admin_can_update_metadata(dataset_access, permission):
    _, factory, services, admin, user = dataset_access
    dataset = create_dataset(services, admin, "editable")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        original = (
            persisted.id,
            persisted.key,
            persisted.owner_user_id,
            persisted.created_at,
            persisted.updated_at,
        )

    updated = services.datasets.update_dataset(
        user.id, dataset.key, "  Résumé  ", "  Description preserved  "
    )

    assert updated.name == "Résumé"
    assert updated.description == "  Description preserved  "
    assert (updated.id, updated.key, updated.owner_user_id, updated.created_at) == original[:4]
    assert updated.updated_at.replace(tzinfo=None) > original[4].replace(tzinfo=None)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.actor_user_id == user.id,
                    AuditEvent.object_type == "dataset",
                    AuditEvent.object_id == dataset.id,
                    AuditEvent.action == "metadata_updated",
                )
            )
            == 1
        )


def test_viewer_cannot_update_and_missing_matches_inaccessible(dataset_access):
    _, factory, services, admin, user = dataset_access
    dataset = create_dataset(services, admin, "readonly")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, "viewer")

    messages = []
    for key in (dataset.key, "missing"):
        with pytest.raises(NotFoundError) as captured:
            services.datasets.update_dataset(user.id, key, "Changed", "Changed")
        messages.append(str(captured.value))
    assert messages == ["Dataset not found", "Dataset not found"]
    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        assert persisted.name == "Readonly"
        assert persisted.description == "About readonly"
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == dataset.id,
                    AuditEvent.action == "metadata_updated",
                )
            )
            == 0
        )


def test_community_admin_can_update_without_resource_grant(dataset_access):
    _, factory, services, admin, _ = dataset_access
    dataset = create_dataset(services, admin, "admin-edit")
    with factory.begin() as session:
        session.execute(text("DELETE FROM grants WHERE resource_type = 'dataset'"))

    updated = services.datasets.update_dataset(admin.id, dataset.key, "Changed", "")

    assert updated.name == "Changed"


def test_current_team_editor_can_update_metadata(dataset_access):
    _, _, services, admin, user = dataset_access
    dataset = create_dataset(services, admin, "team-edit")
    team = services.teams.create_team(admin.id, "Dataset editors")
    services.memberships.create_membership(admin.id, user.id, team.id)
    services.grants.create_grant(admin.id, "team", team.id, "dataset", dataset.id, "editor")

    updated = services.datasets.update_dataset(user.id, dataset.key, "Team edited", "")

    assert updated.name == "Team edited"


def test_update_validation_reuses_creation_rules_without_writes(dataset_access):
    _, factory, services, admin, _ = dataset_access
    dataset = create_dataset(services, admin, "validation")

    with pytest.raises(ValueError):
        services.datasets.update_dataset(admin.id, dataset.key, "   ", "")
    with pytest.raises(ValueError):
        services.datasets.update_dataset(admin.id, dataset.key, "Valid", "x" * 10_001)

    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        assert persisted.name == "Validation"
        assert persisted.description == "About validation"
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == dataset.id,
                    AuditEvent.action == "metadata_updated",
                )
            )
            == 0
        )


def test_audit_failure_rolls_back_metadata_update(dataset_access, monkeypatch):
    _, factory, services, admin, _ = dataset_access
    dataset = create_dataset(services, admin, "rollback")

    def fail_audit(_uow, **_values):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        services.datasets.update_dataset(admin.id, dataset.key, "Changed", "Changed")

    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        assert persisted.name == "Rollback"
        assert persisted.description == "About rollback"


def test_postgresql_dataset_read_list_update_and_rollback(monkeypatch):
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_database_engine(database_url)
    schema = f"dataset_access_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    application_engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(application_engine)
        factory = create_session_factory(application_engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        admin = services.users.create_initial_admin(
            "admin@example.com", "Administrator", "valid password"
        )
        user = services.users.create_user(admin.id, "user@example.com", "User", "valid password")
        direct = create_dataset(services, admin, "direct")
        team_dataset = create_dataset(services, admin, "team")
        team = services.teams.create_team(admin.id, "Editors")
        services.memberships.create_membership(admin.id, user.id, team.id)
        services.grants.create_grant(admin.id, "user", user.id, "dataset", direct.id, "viewer")
        services.grants.create_grant(
            admin.id, "team", team.id, "dataset", team_dataset.id, "editor"
        )

        assert [item.key for item in services.datasets.list_datasets(user.id)] == [
            "direct",
            "team",
        ]
        assert services.datasets.get_dataset(user.id, "direct").id == direct.id
        services.datasets.update_dataset(user.id, "team", "Updated", "PostgreSQL")

        def fail_audit(_uow, **_values):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            services.datasets.update_dataset(user.id, "team", "Rolled back", "")
        with factory() as session:
            persisted = session.get(Dataset, team_dataset.id)
            assert persisted.name == "Updated"
            assert persisted.description == "PostgreSQL"
            assert (
                session.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.object_id == team_dataset.id,
                        AuditEvent.action == "metadata_updated",
                    )
                )
                == 1
            )
    finally:
        application_engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        administration_engine.dispose()
