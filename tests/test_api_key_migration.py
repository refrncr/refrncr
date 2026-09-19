"""REF-40 real Alembic transitions, preservation and fail-closed downgrade."""

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from test_audit_coverage import audit_db  # noqa: F401
from test_dataset_version_migration import config_for

from refrncr.api_keys import ApiKeyService


def test_upgrade_preserves_existing_database_and_empty_downgrade(audit_db):  # noqa: F811
    engine, _, services = audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "0021_ref_40")
        command.downgrade(config, "0020_ref_162")
        before = connection.execute(text("SELECT * FROM audit_events")).all()
        connection.commit()
        assert "api_keys" not in inspect(connection).get_table_names()
        connection.commit()
        command.upgrade(config, "head")
        assert connection.execute(text("SELECT * FROM audit_events")).all() == before
        assert connection.scalar(text("SELECT id FROM users")) == admin.id
        assert "api_keys" in inspect(connection).get_table_names()
        connection.commit()
        command.downgrade(config, "0020_ref_162")
        assert connection.execute(text("SELECT * FROM audit_events")).all() == before
        connection.commit()
        command.upgrade(config, "head")
    service = ApiKeyService(services.users.uow, pepper=b"test-only-pepper!" * 2)
    assert service.create_key(admin.id, "After upgrade").key.status == "Active"


@pytest.mark.parametrize("history", ["key", "anonymous_event"])
def test_populated_downgrade_preserves_history(audit_db, history):  # noqa: F811
    engine, _, services = audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    service = ApiKeyService(services.users.uow, pepper=b"test-only-pepper!" * 2)
    if history == "key":
        service.create_key(admin.id, "Keep history")
    else:
        from refrncr.api_keys import ApiKeyAuthenticationError

        with pytest.raises(ApiKeyAuthenticationError):
            service.authenticate(None)
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "0021_ref_40")
        before = connection.execute(text("SELECT * FROM audit_events")).all()
        connection.commit()
        with pytest.raises(RuntimeError, match="restore a pre-upgrade backup"):
            command.downgrade(config, "0020_ref_162")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0021_ref_40"
        assert connection.execute(text("SELECT * FROM audit_events")).all() == before
        assert "api_keys" in inspect(connection).get_table_names()


def test_database_rejects_anonymous_domain_actor(audit_db):  # noqa: F811
    engine, _, _ = audit_db
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(id,timestamp,actor_user_id,object_type,object_id,action) "
                "VALUES ('attempt',CURRENT_TIMESTAMP,NULL,'user','target','created')"
            )
        )
