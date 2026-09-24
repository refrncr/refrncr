# Mapping tables (REF-177)

Mapping tables are Community/core Datasets with `kind=mapping`. They support:

- `standalone`: source and target values are governed directly by the mapping;
- `linked`: a required source reference table and an optional target reference table.

New mapping tables receive an import-ready schema with Source value, Target value,
Target name and Status Fields. Source value is the unique record identifier, which makes
the initial contract a 1:1 crosswalk. Maintainers can add records manually or use the
existing CSV, Excel and JSON import flow while a Concept is active. Import preview,
validation, replay protection and atomic confirmation are inherited from Record import.

Each publication is an immutable Dataset version with its own effective date. Source and
target system labels describe the operational systems and do not require linked refrncr
tables. Linked table deletion is restricted to avoid broken references.

The Dataset REST representations expose `kind`. Mapping resolution is intentionally not
part of REF-177; REF-92 owns that API contract.
