# ADR-011: Dataset team ownership and independent export access

Status: Accepted. REF-73 merged into main in PR #41.

## Context

[REF-73](https://cleverhorizons.atlassian.net/browse/REF-73) replaces Dataset-specific
viewer/editor/admin authorization from earlier stories. Community teams represent data domains.
Every active installation user must read all Datasets, while maintenance follows current team
membership and export is a separate capability. Existing Dataset metadata/schema mutations do
not implement a general Dataset revision contract.

## Decision

Use `DatasetAuthorizationService` for view, create, maintain, export, ownership and export-access
administration. It depends on persistence-neutral Unit of Work ports (ADR-010), not Flask or ORM
queries. Existing Dataset resource checks delegate to these decisions; generic Grants remain
unchanged for other resource types. A future API can reuse the services; REF-73 adds no API.

Require one `owner_team_id`. Keep `owner_user_id` solely as historical creator metadata.
Current owning-team members maintain/export; active Community administrators additionally manage
ownership and export access. Every active user reads and may create for an eligible team.

Persist explicit export access in `DatasetExportAccess`, with exactly one user/team subject and
database uniqueness per Dataset and subject. Export access never grants maintenance. Retain old
Dataset Grants as inert historical rows without conversion or combining authorization models.

Reassignment locks the Dataset using the existing adapter patterns and compares the submitted
`expected_owner_team_id` with persisted ownership in the same transaction, then applies an SQL
compare-and-set. PostgreSQL uses row locks; SQLite serializes writes with `BEGIN IMMEDIATE`.
Team and user locks stabilize target existence and authority through commit. Lock order follows
Dataset, team, user; multiple user locks are sorted. Stale requests and no-ops write nothing.
There is no general Dataset revision. The expected owner compares current identity, so an A→B→A
sequence is intentionally indistinguishable from unchanged A under this contract.

Ownership, export access and audit events share a transaction. Audit details hold stable owning
team/subject/Dataset identifiers; historical audit rows remain unchanged. Owning-team deletion
is blocked by the service and restrictive FK. User soft deletion retains historical creator
metadata without conferring rights or blocking that lifecycle operation.

## Consequences

Membership changes take effect without grant synchronization. Dataset overview joins resolve
owning-team names in bulk. Services recheck mutation authority inside the locked transaction;
export authorization is evaluated in its existing consistent read snapshot.

Migration `0012_ref_73` requires an operator mapping for creators with zero or multiple current
teams. Resolve every Dataset before DDL; SQLite DDL/backfill and foreign-key verification run
inside one explicit transaction, as PostgreSQL already supports. No team or membership is
invented. Populated downgrade fails closed because old code would reactivate old Dataset Grants;
restore a pre-upgrade backup instead. See the [operator guide](../development/dataset-governance.md).

These are shared Community/core capabilities, without tenant identifiers or Cloud infrastructure.

## Alternatives considered

- Per-member Dataset Grants: duplicate membership and permit stale authority.
- Export as a fourth role: incorrectly implies maintenance or role inheritance.
- General Dataset revision: expands concurrency across unrelated mutations that do not use it.
- Automatic fallback team or Grant conversion: invents ownership or broadens export rights.
- Silent populated downgrade: loses new access semantics and can reactivate historical rights.
