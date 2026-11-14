"""Programmatic administrator operations for trusted integrators."""

from sqlalchemy.orm import Session, sessionmaker

from refrncr.persistence import SqlAlchemyUnitOfWorkFactory
from refrncr.services import UserService


def recover_administrator_password(
    session_factory: sessionmaker[Session], email: str, new_password: str
) -> None:
    """Replace an eligible administrator password without translating domain errors."""
    users = UserService(SqlAlchemyUnitOfWorkFactory(session_factory))
    users.recover_administrator_password(email, new_password)
