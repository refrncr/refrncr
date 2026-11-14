"""Programmatic administrator operations for trusted integrators."""

from sqlalchemy.orm import Session, sessionmaker

from refrncr.app import create_community_services
from refrncr.services import ResourceTypeRegistry


def recover_administrator_password(
    session_factory: sessionmaker[Session], email: str, new_password: str
) -> None:
    """Replace an eligible administrator password without translating domain errors."""
    services = create_community_services(session_factory, ResourceTypeRegistry())
    services.users.recover_administrator_password(email, new_password)
