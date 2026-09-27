# Mapping tables (REF-177)

Mapping tables are Community/core Datasets with `kind=mapping`. They support:

- `standalone`: source and target identifiers are described with free text;
- `linked`: a required source reference table and field, plus an optional target
  reference table and field. Without a target table, its identifier remains free text.

Standalone mapping tables start without an assumed schema. A maintainer first defines
one Source identifier Field and at least one Mapping target Field through the regular
Field configuration. The Source identifier is automatically required, unique and the
record identifier, preserving a 1:1 crosswalk without imposing generic Field names.
After defining it, maintainers can add editable Source context Fields alongside Mapping
target Fields. Linked Source context Fields remain inherited and read-only.
Maintainers can then add records manually or use the existing CSV, Excel and JSON import
flow while a Concept is active. Import preview, validation, replay protection and atomic
confirmation are inherited from Record import.

Each publication is an immutable Dataset version with its own effective date. Identifier
descriptions are free text for standalone mappings and for an unlinked target. Linked
tables store the selected field key; deleting a linked table or selected field is
restricted to avoid broken references.

The Dataset REST representations expose `kind`. Mapping resolution is intentionally not
part of REF-177; REF-92 owns that API contract.
