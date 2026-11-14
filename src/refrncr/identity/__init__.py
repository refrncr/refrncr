"""Identity-domain services, errors, and supporting behavior."""

from refrncr.identity.authentication import AuthenticationService
from refrncr.identity.errors import (
    IdentityAuthorizationError,
    IdentityConflictError,
    IdentityError,
    IdentityNotFoundError,
    IdentityValidationError,
    InvalidLifecycleError,
)
from refrncr.identity.memberships import MembershipService
from refrncr.identity.sessions import SessionService
from refrncr.identity.shared import (
    AuthenticatedPrincipal,
    normalize_email,
    normalize_team_name,
    require_admin,
    validate_password,
)
from refrncr.identity.teams import TeamService
from refrncr.identity.users import UserService

__all__ = [
    "AuthenticatedPrincipal",
    "AuthenticationService",
    "IdentityAuthorizationError",
    "IdentityConflictError",
    "IdentityError",
    "IdentityNotFoundError",
    "IdentityValidationError",
    "InvalidLifecycleError",
    "MembershipService",
    "SessionService",
    "TeamService",
    "UserService",
    "normalize_email",
    "normalize_team_name",
    "require_admin",
    "validate_password",
]
