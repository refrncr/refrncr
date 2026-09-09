from alembic import context
from sqlalchemy import engine_from_config, pool

from refrncr import models  # noqa: F401
from refrncr.db.base import Base

config = context.config
target_metadata = Base.metadata


def _run_online_migrations(connection) -> None:
    sqlite = connection.dialect.name == "sqlite"
    if sqlite and connection.in_transaction():
        raise RuntimeError("SQLite migrations require ownership of the connection transaction")
    if sqlite:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
            raise RuntimeError("Could not suspend SQLite foreign-key enforcement for migration")
        connection.commit()
    try:
        if sqlite:
            # sqlite3 legacy transaction control otherwise autocommits initial DDL.
            with connection.begin():
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                context.configure(
                    connection=connection, target_metadata=target_metadata, transactional_ddl=True
                )
                with context.begin_transaction():
                    context.run_migrations()
                if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
                    raise RuntimeError("Migration would break foreign-key relationships")
        else:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        if sqlite:
            if connection.in_transaction():
                connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
                raise RuntimeError("Could not restore SQLite foreign-key enforcement")
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            if violations:
                raise RuntimeError("Migration left broken foreign-key relationships")
            connection.commit()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_online_migrations(supplied_connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_online_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
