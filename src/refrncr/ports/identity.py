"""Persistence-neutral port contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class OrganisationRecord(Protocol):
    id: str


class UserRecord(Protocol):
    id: str
    email: str
    display_name: str
    password_hash: str
    role: str
    is_active: bool
    is_deleted: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class LoginSessionRecord:
    id: str
    user_id: str
    token_hash: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


class LoginSessionStore(Protocol):
    def create(
        self,
        *,
        user_id: str,
        token_hash: str,
        csrf_token: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> LoginSessionRecord: ...

    def resolve(
        self, token_hash: str, now: datetime
    ) -> tuple[UserRecord, LoginSessionRecord] | None: ...

    def revoke(self, token_hash: str, revoked_at: datetime) -> None: ...

    def cleanup(
        self, cutoff: datetime, cursor: str | None, batch_size: int
    ) -> tuple[int, str | None]: ...


class TeamRecord(Protocol):
    id: str
    name: str
    name_key: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MembershipRelationship:
    user_id: str
    user_name: str
    team_id: str
    team_name: str


class MembershipRecord(Protocol):
    id: str
    user_id: str
    team_id: str


class GrantRecord(Protocol):
    id: str
    subject_type: str
    subject_id: str
    resource_type: str
    resource_id: str
    permission: str
    updated_at: datetime


class UserOperations(Protocol):
    def get(self, user_id: str, *, for_update: bool = False) -> UserRecord | None: ...

    def find_by_email(self, email: str) -> UserRecord | None: ...

    def list_by_email(self) -> list[UserRecord]: ...

    def count(self) -> int: ...

    def count_active_admins(self) -> int: ...

    def create(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        role: str,
        theme_preference: str,
        is_active: bool,
        is_deleted: bool,
    ) -> UserRecord: ...

    def replace_password_hash(
        self, user_id: str, expected_hash: str, replacement_hash: str
    ) -> bool: ...

    def update_theme_preference(self, user_id: str, theme_preference: str) -> UserRecord: ...

    def revoke_active_sessions(self, user_id: str, revoked_at: datetime) -> None: ...

    def get_community_organisation(self) -> OrganisationRecord | None: ...

    def create_community_organisation(self) -> OrganisationRecord: ...

    def lock_community_setup(self) -> OrganisationRecord | None: ...


class TeamOperations(Protocol):
    def get(self, team_id: str, *, for_update: bool = False) -> TeamRecord | None: ...

    def list_by_name(self) -> list[TeamRecord]: ...

    def create(self, *, name: str) -> TeamRecord: ...

    def delete(self, team: TeamRecord) -> None: ...


class MembershipOperations(Protocol):
    def list_relationships(self) -> tuple[MembershipRelationship, ...]: ...

    def team_ids_for_user(self, user_id: str) -> tuple[str, ...]: ...

    def get(self, membership_id: str, *, for_update: bool = False) -> MembershipRecord | None: ...

    def create(self, *, user_id: str, team_id: str) -> MembershipRecord: ...

    def delete(self, membership: MembershipRecord) -> None: ...

    def list_team_members(self, team_id: str) -> list[tuple[MembershipRecord, UserRecord]]: ...

    def list_eligible_users(self, team_id: str) -> list[UserRecord]: ...


class GrantOperations(Protocol):
    def get(self, grant_id: str, *, for_update: bool = False) -> GrantRecord | None: ...

    def create(
        self,
        *,
        subject_type: str,
        subject_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> GrantRecord: ...

    def delete(self, grant: GrantRecord) -> None: ...

    def list_for_team(self, team_id: str, *, for_update: bool = False) -> list[GrantRecord]: ...


class ApiKeyRecord(Protocol):
    id: str
    owner_id: str
    name: str
    verifier: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    revision: int


class ApiKeyOperations(Protocol):
    def get(self, key_id: str, *, fresh: bool = False) -> ApiKeyRecord | None: ...
    def get_owned(self, owner_id: str, key_id: str) -> ApiKeyRecord | None: ...
    def list_owned(self, owner_id: str) -> tuple[ApiKeyRecord, ...]: ...
    def active_owned(self, owner_id: str, now: datetime) -> tuple[ApiKeyRecord, ...]: ...
    def create(self, **values) -> ApiKeyRecord: ...
