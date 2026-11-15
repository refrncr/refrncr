from __future__ import annotations

from collections.abc import Iterable

from refrncr.ports import AuthorizationSubject

PERMISSION_LEVELS = {"viewer": 1, "editor": 2, "admin": 3}


def validate_permission(permission: str) -> None:
    if permission not in PERMISSION_LEVELS:
        raise ValueError("Permission must be viewer, editor or admin")


def highest_permission(permissions: Iterable[str]) -> str | None:
    return max(set(permissions), key=PERMISSION_LEVELS.__getitem__, default=None)


def permits(subject: AuthorizationSubject, permissions: Iterable[str], required: str) -> bool:
    validate_permission(required)
    if not subject.exists or not subject.active or subject.deleted:
        return False
    if subject.community_admin:
        return True
    effective = highest_permission(permissions)
    return effective is not None and PERMISSION_LEVELS[effective] >= PERMISSION_LEVELS[required]
