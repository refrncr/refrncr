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
target columns remain unchanged, and the import never adds or removes rows. Every caller must
supply the confirmation token from the preview. A file without changes has no confirmation
button and is rejected by the service without persisting changes or emitting a domain audit.
Successful imports report changed target **records**, not total input rows or individual cells.
One transactional `dataset.mapping_targets_updated` event records the format and changed/unchanged
row counts (only rows present in the file). Source and
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
maintainers explicitly choose **Update to Vn** to upgrade (REF-301). The source owner's
usage view belongs to REF-302.

## Updating a source

The update action selects the latest non-cancelled source publication under the existing
Dataset locks, including Scheduled versions and jumps such as V1 to V3. It creates a draft
or reuses the active one. Matching uses the canonical source identifier: existing target
values, including draft edits, survive; source context values are refreshed. New identifiers
start with empty targets. Removed identifiers retain their values as draft tombstones labeled
**Removed from source** and are omitted from publication. Identifiers returning during further
updates to the same draft recover their targets. They cannot be manually restored into a
source snapshot that does not contain them.

Drafts expose Unmapped and Removed from source counts and filters. Unmapped means a row has
no target values. The review shows the source transition and the same counts, including when
only the source link changed. Optional empty targets can be published with a counted warning;
required targets still use the existing record validation. Publication defaults to the source
effective date, or today when that date is in the past, and rejects dates before the source.
A newer Current source makes the notice a warning even when a still newer Scheduled version
is available to update to. Published mappings and their history remain immutable.
Rebuilding a draft after discard or cancellation restores source/target field roles and
source-field links along with the snapshot values.

An update removes selected context fields that are missing or have changed datatype in the
new source. The draft and review retain an explanation for each removal, including the source
version, until publication or discard. A still-available field can be added again with its new
datatype. The source identifier must remain required and unique, with its original datatype;
otherwise the shared notice explains the blocker and remediation without offering an update
button. A direct update request is also rejected atomically and returns to the mapping notice.
Published mappings whose draft already uses the newest source link to that draft instead of
offering another update. Successful updates report unmapped and removed counts. The publication
date help names the earliest permitted date and the pinned source version. Updates use the existing revision, authorization,
CSRF, audit and rollback machinery. Like existing Dataset version routes,
`POST /datasets/<key>/source/update` returns the same non-disclosing 404 for missing
and inaccessible Datasets; malformed/stale revisions return 400/409. The `dataset.mapping_source_updated` event includes only
the old and new source version numbers. No REST write endpoint is added. Revision `0033_ref_301` adds nullable JSON
`dataset_concepts.source_update_notes` for draft explanations. Existing drafts need no backfill;
publication and discard remove the notes with the concept. Downgrade drops only this metadata.

## Presentation and authorization

Records and record review share one table with source/target column groups, a muted source
background, white target values and a white direction strip. Target group and column headings
use the normal table-heading background, distinct from the white values. Both groups have their own frame;
row lines continue through the strip. Status/Change remain outside the groups, with the normal
record metadata style. On phones, each row becomes one source-then-target card.
Fields use equally framed, vertically stacked source and target groups with separate add actions;
the source table stays muted and the target table stays white. Settings
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


For REF-301 source-update checks, start the separate disposable fixture with
`PYTHONPATH=tests .venv/bin/python tests/browser_mapping_updates_app.py` instead. Sign in
with its test account (`admin@example.com`, `correct horse battery staple`) and check:

1. `/datasets/blocked-mapping/records`: the notice explains the invalid identifier and
   remediation, with no **Update to V2** button.
2. `/datasets/mapping-browser/records`: click **Update to V2**. The draft reports
   **Updated to V2: 1 unmapped, 1 removed from source.** The GBP row shows **Added** and
   **Unmapped**; mapping filters sit beside the change filters and work by keyboard.
3. Follow **View current version**: the notice contains **Draft already uses V2** and
   links to the draft, with no update button.
4. Open **Review & publish**, then **Publish version**: the effective-date help names
   Currencies V2 and its date (seven days after the fixture starts). Check the filters
   also fit at a 320-pixel viewport. No publication is needed for these checks.

These browser checks were performed for REF-301 review 1. The corresponding rendered-output,
service, migration and rollback regressions live in `tests/test_mapping_source_updates.py`.
The existing full-row loading for draft mapping counters remains a performance follow-up.
