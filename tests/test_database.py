from sqlalchemy import text

from refrncr.db.session import create_database_engine, create_session_factory


def test_sqlite_database_connection():
    engine = create_database_engine("sqlite:///:memory:")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        result = session.execute(text("SELECT 1"))

        assert result.scalar_one() == 1


def test_postgresql_database_connection():
    engine = create_database_engine(
        "postgresql+psycopg://refrncr:refrncr@localhost:5432/refrncr_test"
    )
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        result = session.execute(text("SELECT 1"))

        assert result.scalar_one() == 1
