"""Existing SQLite/PostgreSQL installations gain private visits without data loss."""

from alembic import command
from sqlalchemy import inspect, select, text
from support import config_for

from refrncr.models import DatasetVisit
from refrncr.overview import OverviewService


def test_visits_upgrade_and_cascades(database_context):
    c = database_context
    with c.engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0033_ref_301")
        assert "dataset_visits" not in inspect(connection).get_table_names()
        before = connection.execute(text("SELECT id, key, name FROM datasets")).all()
        connection.commit()
        command.upgrade(config, "head")
        assert connection.execute(text("SELECT id, key, name FROM datasets")).all() == before
        assert connection.scalar(text("SELECT count(*) FROM dataset_visits")) == 0
        foreign_keys = inspect(connection).get_foreign_keys("dataset_visits")
        assert {fk["referred_table"] for fk in foreign_keys} == {"users", "datasets"}
        assert all(fk["options"]["ondelete"] == "CASCADE" for fk in foreign_keys)
        connection.commit()
    service = OverviewService(c.s.datasets.uow, c.s.authorization.datasets)
    service.record_visit(c.a.id, key=c.d.key)
    with c.sessions() as session:
        assert session.get(DatasetVisit, (c.a.id, c.d.id)).last_visited_at.tzinfo is not None
    with c.engine.begin() as connection:
        connection.execute(text("DELETE FROM datasets WHERE id=:id"), {"id": c.d.id})
    with c.sessions() as session:
        assert list(session.scalars(select(DatasetVisit))) == []
