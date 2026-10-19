"""Shared domain errors used across service modules."""


class UserManagementError(Exception):
    """Base class for safe errors that may be shown to an authenticated user."""


class ValidationError(UserManagementError, ValueError):
    """A request contains a value that violates a domain constraint."""

    def __init__(self, message, *, field=None):
        self.field = field
        super().__init__(message)
