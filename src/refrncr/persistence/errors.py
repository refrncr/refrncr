"""Persistence-domain errors exposed through compatibility contracts."""

from refrncr.errors import DomainError


class PersistenceError(DomainError):
    """Persistence-boundary failure safe to expose as a domain error."""
