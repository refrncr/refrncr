# ADR-012: Validate audit contracts in services and audit completed export preparation

Status: Accepted for REF-149. The product owner approved the separate export audit transaction;
REF-149 merged into `main` through PR #42 (commit `5ffc192`). Part-2 review and full local
SQLite/PostgreSQL 16 validation are recorded in its historical reports. REF-39 builds its read
interface on these merged contracts without changing this decision.

## Context

REF-149 requires explicit coverage decisions, safe event contracts and exactly one authorized
`dataset.export_requested` before response streaming. The existing export creates a complete
bounded-memory artifact in a PostgreSQL repeatable-read or explicit SQLite read snapshot. Its
read-only Unit of Work closes without committing writes. Flask previously performed spool
readability preflight after service return. ADR-010 owns mutation/audit atomicity; ADR-011 pins
export authority and content to one coherent snapshot.

A new audit write inside the SQLite read transaction can fail when another writer has committed
(`SQLITE_BUSY_SNAPSHOT` in WAL mode). A separate writer while a default SQLite read snapshot remains
open can fail to commit (`SQLITE_BUSY`). Holding a serialized writer throughout generation would
block unrelated writers for the complete export. These are concrete transaction-boundary changes,
not a reason to remove coherent snapshots or buffer unbounded data.

## Decision

Use the existing `AuditEvent` and Unit of Work with one framework-independent event/operation
catalog. Services validate and normalize their strictly allowlisted context through `record_audit`
before passing it to the adapter. Historical names and rows remain unchanged. Mutations and their
audit continue committing/rolling back together. Explicit operation registrations and an independent
static inventory guard make unclassified service additions fail in the configured pytest CI step.

For export only, prepare the artifact and perform its full bounded readability preflight within
the existing authorized read snapshot. Close that UoW, then commit an audit-only write UoW before
the service returns the artifact. The trusted actor and Dataset UUID come from the prepared request;
format is allowlisted. No new authorization decision is taken after the snapshot. A later revoke
retains ADR-011 semantics: it affects subsequent exports, not a previously authorized preparation.

Close the spool and return no artifact if preparation, preflight, snapshot closure or audit commit
fails. After a prepared request event commits it remains accurate even if response construction or
streaming fails. Log exceptions observed during stream iteration or cleanup once through a fixed technical
diagnostic without exception payloads. Socket errors outside that iterator belong to serving-stack
diagnostics; the application cannot reliably detect every disconnect. Never emit a completion event or claim client receipt.

Confirmed Record import emits one `dataset.records_imported` summary in the same write UoW as all
inserted Records. Historical per-Record `created` events remain stored; new imports no longer emit
those events. Manual Record creation is unchanged.

## Consequences

Export retains bounded memory, coherent contents and snapshot authorization on SQLite/PostgreSQL.
An audit writer is acquired only after reading finishes. A process failure between preparation and
audit commit produces no download/event; a process/transport failure after commit can leave an event
without client receipt, which matches the deliberate request semantics. This is not a distributed
transaction or a download-completion guarantee.

No schema migration, new persistence port, dependency, API, Cloud tenant column, event bus or logging
platform is needed. The annotation of existing JSON context now includes bounded integer counts.
REF-31 owns structured security logging; REF-39 owns history presentation. See the
[audit policy and coverage procedure](../development/audit-policy.md).

## Alternatives considered

- Insert into the open read snapshot: SQLite upgrade/write conflicts and changed read-UoW semantics.
- Commit a second writer before closing the snapshot: default SQLite readers can block the commit.
- Hold a serialized write transaction for all generation: unnecessarily long SQLite write locks.
- Audit before artifact/preflight success: misleading successful events for preparation failures.
- Audit in Flask or after streaming: reusable services lose the authoritative audit decision, or the
  required pre-response durability is lost; full client receipt still cannot be established.
- Retry/rebuild snapshots after write conflicts: more work and altered authorization/snapshot timing
  without improving the approved prepared-request semantics.

## REF-155 Concept lifecycle exception

Pending Concept exports pin their Concept UUID and recheck identity and current
maintenance authority under the Dataset lock immediately before their prepared-request
audit. Discard winning this boundary returns no artifact or success export event.
This implements REF-155's explicit invalidation requirement using the existing second
UoW, without changing coherent preparation, Current/historical export or already accepted
streaming semantics. See the archived REF-155 implementation notes.
