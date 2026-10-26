"""Singleton installation settings migrate new and populated databases on both engines."""

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from support import config_for

from refrncr.db.base import Base


@pytest.mark.parametrize("populated", [False, True])
def test_installation_policy_migration(database_audit_db, populated):
    engine, _, services = database_audit_db
    if populated:
        services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0034_ref_238")
        assert "installation_settings" not in inspect(connection).get_table_names()
        connection.commit()
        command.upgrade(config, "head")
        assert (
            connection.scalar(text("SELECT export_access_policy FROM installation_settings"))
            == "everyone"
        )
        assert connection.scalar(text("SELECT COUNT(*) FROM installation_settings")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM users")) == int(populated)
        connection.commit()
        for sql in (
            "UPDATE installation_settings SET export_access_policy = 'invalid'",
            "INSERT INTO installation_settings VALUES "
            "('00000000-0000-0000-0000-000000000002', 'everyone')",
        ):
            with pytest.raises(IntegrityError):
                with connection.begin():
                    connection.execute(text(sql))
        # A repeated upgrade preserves an administrator's configured policy.
        with connection.begin():
            connection.execute(
                text("UPDATE installation_settings SET export_access_policy = 'team_grants'")
            )
        command.upgrade(config, "head")
        assert (
            connection.scalar(text("SELECT export_access_policy FROM installation_settings"))
            == "team_grants"
        )


def test_empty_installation_full_migration_chain(database_audit_db):
    engine, _, _ = database_audit_db
    Base.metadata.drop_all(engine)
    with engine.connect() as connection:
        command.upgrade(config_for(connection), "head")
        assert (
            connection.scalar(text("SELECT export_access_policy FROM installation_settings"))
            == "everyone"
        )
