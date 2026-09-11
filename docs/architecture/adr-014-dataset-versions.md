# ADR-014: Complete relational Dataset snapshots and calendar validity

Status: REF-83 foundation is merged in `main` (`a3f8fed`). The REF-84 extension below
is local and uncommitted; see its implementation report for validation. GitHub CI
remains the independent pre-merge gate.

## Context

The working set comprises `datasets`, `dataset_fields`,
`dataset_business_key_fields`, `dataset_records` and `dataset_record_values`.
Fields and Records have stable UUIDs. Identifier components are ordered by key.
Values use REF-145 canonical keys; null is the absence of a value row.
No Dataset revision exists in the preceding schema (`0014_ref_145`).
All supported content writers lock the Dataset before changing children.

The product owner clarified REF-83 on 2026-09-10: only active owning-team members
and active Community administrators edit concepts, publish and cancel. The earlier
editor/publisher distinction is withdrawn. `DatasetAuthorizationService` remains
authoritative. Historical viewer/editor/admin Grants stay inert; export access
does not authorize changes. No new role is introduced.

## Decision

Store complete versions in four relational tables: version metadata and Dataset
name/description, snapshot Fields (including identifier membership), snapshot
Records, and snapshot values. Snapshot UUIDs retain source Field/Record identity
within the version; they have no foreign key to mutable children. Composite foreign
keys prevent cross-version values. Snapshot values store canonical keys and canonical
text, decoded through REF-145, without approximate Numeric reconstruction.

Only the Dataset parent references the working set. REF-141 deletion removes all
operational versions through that parent; cancellation retains all rows. Audit
objects reference the stable Dataset UUID without a Dataset foreign key.

Publication and cancellation use one serialized write Unit of Work and the existing
Dataset → team → actor lock order. A positive Dataset revision advances with content
changes and version lifecycle changes. Publication checks the expected revision
under the lock. A Dataset/number unique constraint backs sequential allocation;
cancelled numbers remain allocated. Rollback consumes no number.

Validity uses UTC calendar dates, from the existing aware UTC clock, independently
of browser display timezone. Cancellation alone is persisted as actor/timestamp.
Other statuses and exclusive end boundaries derive from non-cancelled successors.
No worker or scheduled state transition is required. Current resolution never falls
back to a future or expired snapshot. Repeated effective dates are ordered by version
number: an earlier same-date version has an empty validity interval and remains
historical. It cannot be returned as effective on any calendar date.

Validate complete contents through existing Dataset/Field validators and the same
Record canonicalization/identifier rules used by manual editing and import.
Compare canonical content and stable identities, ignoring ownership/access and
timestamps. Persist only added/changed/removed counts, without before/after payloads.

Upgrade stops all writers, validates every Dataset before version writes, and creates
system-published Version 1 only after the full preflight succeeds. Migration owns
one transaction on both engines and uses no invented user or domain audit actor.
The existing policy classifies operator upgrades as technical operations.

Published read composition selects concept or one explicit/current snapshot under the same
repeatable read transaction as authorization. All existing list/detail/Field/Record services
use that selection, so ordinary reads cannot accidentally reach working data. Explicit version
navigation remains attached to the selected snapshot; snapshot source IDs never resolve live
Fields or Records. Scheduled metadata is administrator-only, including a future end boundary
that would otherwise disclose a private successor. Export uses the same selection and current
export capability while preserving ADR-012's separate prepared-request audit transaction.

## Consequences

Storage grows linearly with the complete contents of each publication. Synchronous
publication holds a Dataset writer lock for validation/copying; SQLite serializes
all writers. Snapshot comparison requires linear work. No asynchronous large-Dataset
workflow, delta storage, retention, API or Cloud infrastructure is introduced.
Immutable service results and append-only persistence operations are authoritative;
missing UI controls alone are insufficient. Current permissions govern reads.

## Alternatives considered

JSON blobs obscure relational integrity and independent streaming/export reads.
Delta storage requires reconstruction and is explicitly outside scope. Mutable
status flags require jobs to remain correct. Timestamps alone do not provide a
monotonic optimistic revision. Reusing live Field foreign keys would prevent
historical independence after edits or deletions.

## REF-84 extension (product decisions 2026-09-11)

Detailed review computes a diff on demand from these existing relational snapshots.
It binds the active concept identity/revision and pinned baseline identity/status, including
Scheduled activation, and uses existing coherent read transactions and authorization.
The diff itself adds no persisted diff, cache or alternative publication path.
The subsequent explicit concept lifecycle is specified separately in [ADR-015](adr-015-explicit-dataset-concepts.md).

The shared REF-83/REF-84 classifier compares canonical Record values only in surviving
Fields with the same canonical datatype; structural schema changes and datatype changes
are Field changes. Retyped Field values are excluded from Record counts and changed-value
details because they are not comparable. Relative
Field moves use deterministic longest-increasing-subsequence matching. Historical stored
summary counts are not rewritten. The existing publication service accepts an optional
review identity and validates it under its established lock; review remains optional.

A successful new review commits one bounded `dataset.version_diff_viewed` event before
returning its result. This is the explicitly approved REF-149 ordinary-read exception;
child reads have no additional events, and audit failure returns no result. Implementation,
query/memory limits and validation are in `docs/development/ref-84-report.md`. These choices extend this decision's snapshot/read boundary. ADR-015 records the
subsequent persistent concept lifecycle approved in the manual UI review.
