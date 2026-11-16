"""Persistence-neutral errors for the hierarchy domain."""

from refrncr.errors import ConflictError, DomainError, NotFoundError, ValidationError


class HierarchyError(DomainError):
    """Base class for hierarchy-domain failures."""


class HierarchyNotFoundError(HierarchyError, NotFoundError):
    """A hierarchy resource does not exist or is deliberately concealed."""


class HierarchyConflictError(HierarchyError, ConflictError):
    """Hierarchy work conflicts with current domain state."""


class HierarchyValidationError(HierarchyError, ValidationError):
    """Hierarchy input violates a domain constraint."""
