from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    # SQLite NUMERIC affinity rounds large integers/Decimals. Compare exact canonical
    # decimal text inside the database sort; LIMIT/OFFSET still runs in SQLite.
    def compare_number(left, right):
        a, b = Decimal(left), Decimal(right)
        return (a > b) - (a < b)

    dbapi_connection.create_collation("refrncr_number", compare_number)
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_database_engine(database_url: str, **engine_options: Any) -> Engine:
    engine = create_engine(database_url, **engine_options)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
