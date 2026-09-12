# ADR-013: Centralize canonical Field types and retain existing storage keys

Status: Accepted for the local REF-145 implementation; uncommitted and unmerged.

## Context

REF-145 requires nine technical Field types shared by manual editing, import
adapters and future interfaces. Four existing types use typed persistence columns
and canonical binary uniqueness keys. Decimal's historical ID is `number`; reads
already use its key for exactness across SQLite and PostgreSQL. Replacing that ID
or rewriting populated Record values would risk identifier compatibility.

## Decision

Use one standard-library-only `refrncr.datatypes` catalog for stable IDs/labels,
strict bounded parsing, canonical text/key encoding, typed comparison, nulls,
safe errors and identifier eligibility. Existing application services translate
its errors through their public validation boundary. No new service, framework,
type hierarchy or persistence port is necessary.

Retain all existing IDs and byte keys. Decimal keeps ID `number` and is labelled
Decimal. New types store canonical text in the existing text value column and
their own prefixed canonical keys. The Field definition remains authoritative for
their datatype; adapters decode typed Python values. Existing typed columns and
the exactly-one-value constraint remain intact. Sorting uses typed values, not
database-specific text casts or lexicographic canonical keys.

Migration 0014 changes only the Field type CHECK constraint after validating
historical state, within the existing transactional migration environment. No
Record content/key/audit backfill occurs. Unsupported populated downgrades fail.

The existing Field value-existence port guards datatype changes under the Dataset
lock. Float cannot be an identifier component through any mutation entrypoint.
Record revisions, authorization and mutation/audit UoW boundaries are retained.

## Consequences

Community and future Cloud adapters share canonical behavior without tenant
composition. SQLite integer limits, Numeric approximation and timezone driver
coercion cannot redefine new-type equality or ordering. Binary uniqueness keys
and identifiers remain compatible. Canonical text is duplicated in key payloads,
as it already is for String; bounded decoding checks key/column consistency.

New types do not gain native SQL-column ordering/filter indexes. Existing Record
pages already sort in application memory; exports retain bounded external sort.
A future indexed filtering requirement must justify an adapter/schema change
without altering canonical semantics. Field metadata still has no general
revision contract. Configurable output/source formats remain separate stories.

## Alternatives considered

- Add five typed database columns: unnecessary schema expansion, with additional
  SQLite/PostgreSQL precision, integer-range and timezone adaptation obligations.
- Replace all value storage or rename `number`: unnecessary rewrites and risk to
  existing uniqueness keys, identifier bytes and historical migrations.
- Add parsers in web/import/persistence separately: competing validation paths
  would make interface and engine equivalence difficult to maintain.
- Sort canonical bytes or cast them in each engine: incorrect numeric ordering
  and inconsistent precision/resource behavior.

See [the datatype guide](../development/field-datatypes.md) for exact contracts,
operational upgrade instructions and the future-datatype procedure.


## REF-154 source-adapter extension

CSV/XLSX source interpretation uses a separate immutable `source_formats`
allowlist whose output must pass this ADR's canonical datatype contract. It
never widens manual entry or rewrites canonical keys. Remembered import proposals
are separate operational rows attached to Field identity through the existing
Field persistence port, not Field configuration or published snapshot data.
Only the locked import confirmation transaction writes proposals; Concept
initialization preserves them for surviving Field identities. See the import
security guide for migration 0017, lifecycle and rollback details.
