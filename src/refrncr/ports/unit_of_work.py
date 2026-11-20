"""Persistence-neutral port contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Protocol

from .audit import AuditReadOperations
from .datasets import (
    DatasetBusinessKeyOperations,
    DatasetExportAccessOperations,
    DatasetFieldOperations,
    DatasetOperations,
    DatasetRecordOperations,
    InstallationSettingsOperations,
    OverviewOperations,
)
from .hierarchies import HierarchyOperations
from .identity import (
    ApiKeyOperations,
    GrantOperations,
    MembershipOperations,
    TeamOperations,
    UserOperations,
)
from .versions import ConceptOperations, VersionDiffOperations, VersionOperations


class UnitOfWork(Protocol):
    installation_settings: InstallationSettingsOperations
    write: bool
    api_keys: ApiKeyOperations
    overview: OverviewOperations
    concepts: ConceptOperations
    version_diff: VersionDiffOperations
    versions: VersionOperations
    audit_history: AuditReadOperations
    users: UserOperations
    teams: TeamOperations
    memberships: MembershipOperations
    grants: GrantOperations
    datasets: DatasetOperations
    dataset_fields: DatasetFieldOperations
    dataset_business_keys: DatasetBusinessKeyOperations
    dataset_records: DatasetRecordOperations
    hierarchy: HierarchyOperations
    dataset_export_access: DatasetExportAccessOperations

    def now(self) -> datetime: ...

    def today(self) -> date: ...

    def begin_export_snapshot(self) -> None: ...

    def flush(self) -> None: ...

    def add_audit(
        self,
        *,
        actor_user_id: str | None,
        object_type: str,
        object_id: str,
        action: str,
        details: dict[str, str | int] | None = None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(
        self, *, write: bool = False, serialized: bool = False, translate_errors: bool = False
    ) -> AbstractContextManager[UnitOfWork]: ...
