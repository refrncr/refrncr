# ADR-024: Mapping sources are immutable published snapshots

Status: accepted for REF-239; supersedes the standalone and live-source decisions in ADR-021.

## Context

A mapping must remain reproducible when its source changes. Reading source working fields
and records makes draft edits leak into dependent mappings, while choosing a source version
only at publication makes the editing baseline ambiguous. REF-239 requires a Current source
at creation and an explicit, immutable source relationship throughout the mapping lifecycle.

## Decision

Reuse Dataset working records, fields, concepts and immutable versions. Store the selected
source version on the working Dataset and on each DatasetVersion. Creation validates and
copies the source's Current snapshot in the existing serialized transaction. Starting a
concept restores the baseline link; publishing validates the source identity and date and
copies the link unchanged. Source contexts are copied from that pinned snapshot.

Published source-version references have a self-referencing foreign key with RESTRICT. The
working pointer is service-validated together with its source dataset identity and required
by a database check for mappings. It has no reverse Dataset-to-DatasetVersion foreign key,
which avoids a cyclic schema creation/deletion dependency. The source dataset remains a
RESTRICT reference. Source field IDs identify immutable snapshot fields rather than mutable
working fields, so the previous working-field foreign key is removed.

Newer-source signals are read-time comparisons against non-cancelled published version
numbers, including future-effective publications. Dataset lists batch these comparisons.
Source changes never mutate a mapping. Existing complete-record imports are rejected for
mappings because row membership and source values belong to the pinned snapshot.

## Consequences

Mapping-only edits remain publishable after the source version expires. Published history
retains its source identity. No background synchronization or new notification infrastructure
is required. Source upgrades and row reconciliation remain REF-301. The technical migration
requires explicit removal of existing mappings and leaves other data intact; no inferred
conversion of old mappings is performed.

Authorization, audit, optimistic revisions, immutable history, export order and transactional
publication use the existing Dataset machinery. The browser uses a shared mapping table
variant and source heading macro for record and review views, plus the same field-table
partial in each source/target block.

## Alternatives considered

- Resolve Current on every read: changes the meaning and values of an existing mapping.
- Select a source during publication: leaves drafts without a stable baseline.
- Store a parallel mapping snapshot model: duplicates version, record and audit machinery.
- Convert historical mappings automatically: needs product decisions about their intended
  source version and contradicts the agreed manual cleanup.

See [mapping operations](../development/mapping-tables.md) and
[REF-239](https://cleverhorizons.atlassian.net/browse/REF-239).
