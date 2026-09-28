# ADR-022: Standalone hierarchy nodes and complete Dataset snapshots

Status: Accepted.

## Context

[REF-179](https://cleverhorizons.atlassian.net/browse/REF-179) introduces one standalone
hierarchy per Community Dataset. Its structure must exist before optional custom Fields
and values. Nodes retain their identities through renames and moves. The Records view
contains leaves; root and branch nodes remain structure even when they have values.

Existing Dataset Records require a field-based identifier and immediately enforce unique
identifiers and unique Field values in the database. Hierarchies allow an optional
identifier and retain inactive values when a leaf becomes a branch or a Field changes
scope. Restoring those values may leave a saved Concept with conflicts that must be
resolved before publication. Reusing mutable Record rows would conflate branches with
Records and conflict with these requirements.

## Decision

Add `hierarchy` to `Dataset.kind`. Reuse Dataset ownership, authorization, metadata,
Fields, optional composite Record identifiers, Concepts, publication, effective dates,
cancellation and audit boundaries. Reference and Mapping behavior remains governed by
its existing validators and storage constraints.

Persist structure in `dataset_hierarchy_nodes`: stable UUID, Dataset UUID, name, nullable
parent UUID and revision. A null parent identifies the single root. A partial unique
index permits at most one root; sibling names are unique within their parent. A composite
self-reference prevents parents from another Dataset. The service checks connectivity,
missing parents and cycles under the existing Dataset writer lock. A publication requires
the root and at least one child.

Reuse Dataset Fields with an explicit `hierarchy_scope`: `leaves` or `all_nodes`.
Store node values separately in `dataset_hierarchy_values` as canonical datatype keys
and text. Composite foreign keys bind each value to a node and Field in the same Dataset.
Applicability is derived from scope and current child relationships; no endpoint flag,
inherited value or persisted inactive flag is introduced. Values remain attached to the
same node when applicability changes. Field datatype changes and deletion remain blocked
while any stored values, including inactive values, exist.

Hierarchy Concepts deliberately have no database uniqueness constraint on values or
business identifiers. Authorized manual value edits reject active conflicts. Structural
or scope changes preserve restored values even when they conflict. The shared hierarchy
validator reports those conflicts and blocks publication. Required and Unique apply
within each Field's scope; a configured composite identifier applies only to leaves.

Extend each complete Dataset version with `dataset_version_hierarchy_nodes` and
`dataset_version_hierarchy_values`. The snapshot contains all nodes and values, including
inactive values, and Field snapshots include scope. Snapshot parent/value foreign keys
are version-scoped. These rows use the same ORM update/delete guards as existing snapshot
rows; Dataset deletion still removes its complete operational history.

Also materialize the leaf projection in existing `dataset_version_records` and
`dataset_version_values` for shared Record reads and counts. A structure-only hierarchy
uses a UUID-derived internal snapshot key when no business identifier exists. This key
does not create a custom Field or an additional Record. Mutable hierarchy nodes never
create `dataset_records` rows. Hierarchy review separately compares stable node identity,
name, parent and canonical values so structure-only changes remain visible.

Starting a Concept from a hierarchy publication restores its complete graph, Field
scopes and values with preserved source identities. Discard clears the mutable graph
alongside other Concept content. The three browser views select one Concept or immutable
version consistently. Hybrid remains read-only; writes require server-side Dataset
authority and an active Concept. Hierarchy mutations bind the expected Dataset revision
and commit their audit event atomically with the change.

## Consequences

- Storage includes complete hierarchy snapshots plus a duplicated applicable leaf
  projection. This keeps existing Record snapshot consumers compatible without counting
  branches as Records.
- Reading, validating and comparing hierarchy content loads the complete selected graph
  and values into memory. Full-path labels additionally grow with tree depth. Publication
  remains synchronous under the Dataset lock; this increment adds no background worker
  or new dependency.
- A Concept may be incomplete or have restored-value conflicts. Published versions cannot.
- Migration `0028_ref_179` adds nullable scope to existing Fields and snapshots, leaving
  their content unchanged. It adds the hierarchy tables without converting existing
  Reference or Mapping data. Downgrade refuses hierarchy Datasets, nodes or scoped Fields
  to prevent content loss; use the matching pre-upgrade backup if rollback is required.
- Hierarchy export, import, public API reads and cross-table references remain separate
  stories. Existing tabular routes must not expose an incomplete hierarchy contract.

## Alternatives considered

- Reusing mutable Dataset Records for all nodes would make branches into Records and
  require weakening established Reference/Mapping uniqueness constraints.
- A JSON graph on the Dataset or version would lose relational ownership checks and
  obscure the existing canonical value contract.
- Storing only applicable values would lose recoverable content across publication.
- Storing only the hierarchy graph in versions would require replacing more existing
  Record snapshot readers. The bounded leaf duplication is explicit and generated in the
  same publication transaction.

Related: [ADR-014](adr-014-dataset-versions.md),
[ADR-015](adr-015-explicit-dataset-concepts.md), and
[developer guide](../development/hierarchy-tables.md).
