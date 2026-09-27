# ADR-021: Mapping tables reuse Dataset lifecycle and Record storage

Status: Accepted for REF-177.

## Context

REF-177 introduces mapping tables as the second refrncr table type. A mapping can be
standalone, or linked to one or more governed reference tables. Mappings need the same
Concept, immutable publication, business-effective selection, authorization, audit,
import and export guarantees as reference tables. REF-92 separately owns resolution API
semantics.

## Decision

`Dataset.kind` distinguishes `reference` and `mapping`. Mapping-specific metadata is
stored on the Dataset: mode, source and target identifier descriptions, optional source
and target reference-table identities, and the selected field keys for linked tables.
Database checks prevent mapping metadata on reference tables. Linked table references
use `ON DELETE RESTRICT` so a table dependency cannot be silently broken. The service
validates field ownership when a linked mapping is created.

Mapping content reuses Dataset Fields and Records. A standalone mapping starts without
Fields so its schema does not assume generic Source value, Target value, Target name or
Status columns. A maintainer defines exactly one Source identifier with a domain-specific
name and one or more Mapping target Fields through the regular Field configuration. The
Source identifier is automatically required, unique and the business key. This makes the
existing guided import, Concept mutation, diff, review, publication, effective-date,
export and audit paths apply without a second parallel persistence model. The first
increment is 1:1 because the source business key is unique.

The existing import workflow writes only to the active Mapping Concept and confirms the
complete validated file atomically. Linked input and target tables are never mutated by
mapping import. Standalone identifier descriptions remain independent of table links so
standalone crosswalks are first-class. A linked source requires a table and field. The
target table is optional; choosing one makes its field selection mandatory, while an
unlinked target retains a free-text identifier description.

## Consequences

- Reference-table behaviour and existing API routes remain compatible; API Dataset
  metadata now exposes the table kind.
- Mapping versions are immutable Dataset versions and therefore inherit point-in-time
  selection.
- Deleting a referenced table is refused while linked mappings depend on it.
- REF-92 can consume published mapping records without owning storage or publication.
- Many-to-many mappings and transformation expressions need a later decision because
  they do not fit the unique source-key contract.

## Alternatives considered

- Separate mapping version and row tables were rejected because they would duplicate
  mature lifecycle, import, audit and effective-date machinery.
- Encoding mappings as ordinary reference tables without an explicit kind was rejected
  because configuration, presentation and API consumers could not distinguish their
  semantics safely.
