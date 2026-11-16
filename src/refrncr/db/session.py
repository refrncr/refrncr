"""Deprecated database-factory imports kept for Cloud compatibility."""

from warnings import warn

from refrncr.persistence.database import create_database_engine, create_session_factory

warn(
    "refrncr.db.session is deprecated; import database factories from refrncr.integration instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ("create_database_engine", "create_session_factory")
