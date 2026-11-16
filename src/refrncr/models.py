"""Deprecated ORM model imports kept for Cloud test compatibility."""

from warnings import warn

from refrncr.persistence.models import AuditEvent, LoginSession, User

warn(
    "refrncr.models is deprecated; use the public contract in refrncr.integration instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ("AuditEvent", "LoginSession", "User")
