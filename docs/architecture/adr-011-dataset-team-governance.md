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

## Amendment: installation export policy (REF-294)

The installation chooses `everyone`, `team_grants` or `admin_grants`. New and existing
installations start with `everyone` after migration `0035_ref_294`. This intentionally
opens file export to every active user. Version visibility still applies: drafts remain
private to maintainers and Scheduled/Cancelled content does not become exportable.
App reading, Dataset maintenance, ownership changes and deletion retain their rules.

`everyone` disables grant administration even for administrators. Explicit grants are
retained but ignored. The stricter policies allow export for administrators, current
owning-team members and explicit user/team grantees. `team_grants` also lets owning-team
members manage grants; `admin_grants` reserves that operation for administrators.

Access-tab visibility, grant administration and ownership changes are independent.
Owning-team members can inspect grants under `admin_grants`, including user email
addresses, without receiving mutation rights. Under `everyone`, the tab displays the
owning team and the open-access explanation without exposing retained grants.

A singleton `installation_settings` row stores the policy, with database constraints for
identity and allowed values. Read authorization uses the caller's Unit of Work/snapshot.
Policy and grant mutations lock the settings row before Dataset, team and user locks.
Policy changes compare the submitted previous value, update with SQL compare-and-set,
and append `installation_settings:export_access_policy_changed` in the same transaction.
Missing settings fail closed rather than silently reopening access. As with owner CAS,
an A→B→A sequence is indistinguishable from unchanged A. No-ops emit no audit event.

Every actual transition requires server-checked confirmation except
`admin_grants` → `team_grants`. Warning text distinguishes opening access, restricting
export, and removing delegated administration. A stale confirmation changes nothing.
Downgrading removes the setting and restores the older administrator-managed grant rules;
retained grants survive. No other installation settings or Cloud infrastructure are added.

**Release condition:** REF-294 and REF-306 must ship together in `0.1.0rc1`. The interface
uses its final export-and-API wording. REF-294 does not change API authorization; REF-306
must make API reads follow the policy before this functionality is released to testers.
