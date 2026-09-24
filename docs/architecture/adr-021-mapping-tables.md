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
stored on the Dataset: mode, optional source and target system labels, and optional
source and target reference-table identities. Database checks prevent mapping metadata
on reference tables. Linked table references use `ON DELETE RESTRICT` so a dependency
cannot be silently broken.

Mapping content reuses Dataset Fields and Records. A new mapping starts with four Fields:
`source_value`, `target_value`, `target_name` and `mapping_status`; `source_value` is the
required, unique business key. This makes the existing guided import, Concept mutation,
diff, review, publication, effective-date, export and audit paths apply without a second
parallel persistence model. The first increment is 1:1 because the source business key
is unique.

The existing import workflow writes only to the active Mapping Concept and confirms the
complete validated file atomically. Linked input and target tables are never mutated by
mapping import. Source and target system labels remain independent of table links so
standalone crosswalks are first-class.

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
