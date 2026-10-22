# Mapping tables

Mapping tables are Community/core Datasets with `kind=mapping`. REF-239 replaces the
standalone setup from REF-177. A new mapping requires a reference dataset with a Current
publication and a required, uniquely identifying field in that publication. A single-field
business key also qualifies. Unpublished and Scheduled-only sources are rejected, including
through direct service calls or forged form submissions.

## Pinned content and lifecycle

Creation copies source identifiers and records from the Current snapshot. Additional source
context fields are selected from that same snapshot, including their original definitions
and values. Source columns and row membership are read-only; maintainers edit target values
and configure target fields. Imports update target values by matching the source identifier.
Source context columns are ignored; unknown identifiers fail preview. Omitted rows and
target columns remain unchanged, and the import never adds or removes rows. Source and
target fields can be reordered within their respective groups;
the persisted order is also the record and export column order.

The working mapping and every immutable publication store `source_version_id`. Starting a
new draft inherits its baseline's source version. Publishing retains it even after it has
become Expired, and rejects an effective date before the source's effective date. Neither a
source draft, a source publication nor the passage of time changes mapping values. See
[ADR-024](../architecture/adr-024-pinned-mapping-sources.md).

The metadata line, review and version history identify the source version. A newer
non-cancelled publication, including Scheduled, immediately produces an in-app notice and
dataset-list badge. Cancelling that Scheduled publication removes its signal. These are
computed on reads and need no notification job. There is no automatic source upgrade;
explicit upgrading belongs to REF-301. The source owner's usage view belongs to REF-302.

## Presentation and authorization

Records and record review share one table with source/target column groups, a muted source
background and a direction column. On phones, each row becomes one source-then-target card.
Fields use vertically stacked source and target groups with separate add actions. Settings
keeps dataset-level source/identifier information above the final Delete dataset section.

Existing dataset permissions, CSRF, revision checks, review binding, audit and transaction
boundaries apply. Mapping-source metadata follows the selected mapping version's visibility;
readers cannot inspect draft or future mapping versions. A source link appears only when the
user can view that dataset. The REST API keeps its existing read-only contract; mapping
resolution remains separate work.

## Upgrade

Before deploying revision `0031_ref_239`, delete **all** existing mapping datasets through the
preceding application version, as agreed for REF-239. Back up the database first using the
normal operations procedure. The migration refuses to proceed while any mapping remains;
it neither deletes nor converts existing mapping data. Reference/hierarchy datasets and
publications are preserved. A downgrade similarly requires mapping datasets to be removed.

The migration adds working/published source-version columns and a mapping-source check.
Published links reference immutable versions with `ON DELETE RESTRICT`. Source field IDs
now identify fields in the pinned snapshot and no longer have a foreign key to mutable
working fields. The source dataset itself remains protected against deletion while in use.

Revision `0032_ref_239` removes only the working source-version non-null check, preserving
existing mappings and values. Current-version selection remains required in the service.
Downgrading to `0031_ref_239` restores the stricter check and requires all mapping source
version pointers to be populated.

## Browser verification

`tests/browser_mapping_app.py` starts a disposable in-memory loopback fixture on port 5193.
With that running, `tests/javascript/mapping_browser.cjs` checks 320/768/1440 pixel layouts,
accessible table rows, field panels and keyboard field ordering. It accepts
`REFRNCR_PLAYWRIGHT_MODULE`, `REFRNCR_BROWSER_EXECUTABLE` and optional
`REFRNCR_BROWSER_OUTPUT` for an existing browser toolchain; no runtime dependency is added.
