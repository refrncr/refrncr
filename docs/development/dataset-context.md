# Dataset context persistence (REF-130)

Dataset overview and detail context are authorized service reads. REF-83 now delegates
content selection to `DatasetVersionService`. Normal browser routes use Current snapshot
content/counts with current Dataset name, description and icon for every user. Administrators
select active concept content through explicit concept routes.
No Current means no visible Dataset for ordinary users. Selection and current authority share
one coherent read transaction. Current snapshots use a batched window query and active
concept identities use one batched query, without per-Dataset lookups. `current_overview` itself still counts
only working Records. Snapshot headers use their publication timestamp and snapshot count;
owning-team names remain current administrative context. See [Dataset versions](dataset-versions.md).
There is no overview pagination; selected Record pages retain the existing canonical sort and
50-row page size, with explicit version links keeping subsequent pages on the same snapshot.

`DatasetRecord` stores only the mutable current working set. Its revision is an optimistic
concurrency counter, not another row/version. The explicitly named `current_overview` contract
must continue to count only this working set when historical/publication storage is introduced.
REF-73 adds required owning-team context without versioning, Cloud context or external dependencies.

Migration `0011_ref_130` adds nullable `datasets.content_updated_at` with `DateTime(timezone=True)`
and deterministically backfills every existing row with its `created_at`, irrespective of audit
history or `updated_at`. New untouched datasets have a null value and resolve to `created_at`.
The separate column is necessary because the existing `updated_at` also tracks name/description
edits through SQLAlchemy defaults and service updates. Audit events retain their existing UTC
insertion timestamps and are not used to reconstruct dataset freshness.

Services assign content freshness inside the same serialized, dataset-locked Unit of Work as:

- record creation, revision-checked editing, and revision-checked deletion;
- successful import confirmation that inserts records (once per import);
- field creation, definition changes, and deletion;
- field identifier membership changes, including the standalone business-key operation;
- existing field-order replacement and move operations that actually change ordering.

Identifier membership is part of field-schema structure. Field/schema no-ops return before
changing freshness. REF-149 Record edits compare canonical values after checking the expected revision; unchanged
values preserve revision, freshness and audit history. Name, description, access grants, historical creator metadata and unrelated
metadata do not touch content freshness. REF-73 ownership reassignment changes metadata updated_at
but not content freshness. Import staging,
preview and failures do not touch it. Any failure, including commit failure after a flush, rolls
back both the mutation and its timestamp. Operation groups never commit independently.

PostgreSQL stores aware timestamps; SQLite returns naive UTC values according to the existing
storage convention. The context result normalizes these to aware UTC. The presentation filter
uses explicit English month names independent of process locale, with an ISO `datetime` attribute
on the semantic `time` element. No browser timezone is consulted.

Upgrade uses an additive column and SQL backfill. Downgrade uses Alembic batch alteration;
the existing migration environment suspends SQLite foreign keys during reconstruction and
validates them afterwards, preserving dependent rows without a new SQLite version requirement.
Tests cover populated upgrade/backfill and
downgrade on SQLite and PostgreSQL, including records, values, fields, identifiers, grants and
audits. Older migration fixtures use the test-only historical-schema constructor before exercising upgrade
because REF-73 blocks populated production downgrades; assertions on downgraded schemas select only historical columns.

## Header layout verification

The shared header now follows the supplied `refrncr-dataset-table-v2.html` visual reference.
Its content, relative timestamps, inline styling, external font loading and unrelated UI are
not copied into the application. Existing dataset names, breadcrumbs, statistics and actions
retain their functional contracts, including absolute English UTC dates and the hidden bullet.

Isolated local Chromium rendered the reference and the real Flask templates using the same
packaged fonts. Both use these typography values:

- title: 28 px Montserrat, weight 600, normal line-height (34 px in this renderer);
- breadcrumb: 12 px Inter, weight 400, normal line-height (14.5 px), uppercase, 0.5 px tracking;
- statistics: 14 px Inter, normal line-height (17 px), muted grey with tabular figures.

DOM Range text bounds measured 0 px breadcrumb-to-title and 8 px title-to-statistics in the
reference. The application reproduces those distances on both dataset headers at 1440 px and
320 px. Text bounds include font ascent/descent space; zero extra gap does not mean glyphs touch.
These measured reference values replace the earlier image-based estimates of 4–6 / 16–20 px.

The reference resets element margins, uses normal font line-height and lets the heading's
8 px bottom margin collapse with the paragraph's 8 px top margin. The earlier application had
explicit 4 px / 16 px gaps and different font sizes, weights and line-height. Merely changing
gaps could not reproduce the reference. The final macro removes the nested heading wrapper,
resets its three children's margins, and adds one 8 px padding above metadata in a flat flex
column. The reference's inline paragraph declaration is implemented in shared CSS instead.
The parent header's action gap and 24 px bottom margin are preserved.

At 320 px the bullet and Updated value wrap together and all four record actions fit in two
rows. The records snapshot still has the previously documented table-related overflow:
document scroll width is 537 px, including a visually hidden table heading positioned beyond
the viewport. This also occurs in the pre-refinement snapshot. Header/action bounds remain
within 320 px; the configuration snapshot has no page overflow. Table overflow remains a
separate follow-up.

## Remaining manual verification

Repeat these checks in the running application with the reference image, zoom/increased text size
and assistive technology. The isolated snapshot checks do not replace interactive verification:

1. At 320 px and desktop width, open the overview, records, configuration, edit and import pages.
   Include empty, populated and long-name datasets. Confirm no horizontal page overflow; wide
   tables scroll within their own container. Confirm right-aligned counts and secondary metadata.
2. Check the Datasets → owning-team display name → dataset name breadcrumb. Only Datasets is linked, with
   restrained green text and no underline. Grey separators are decorative, and the final dataset
   name is current. Check wrapping with a long unbroken name and increased text size.
3. Tab through links and primary actions. Confirm logical order, visible focus, and usable table
   navigation. Confirm screen-reader breadcrumb/current-page and table-heading announcements.
4. Check contrast using existing secondary-text and link tokens. Confirm every timestamp is
   English UTC and names containing HTML-like characters display as text.
5. Repeat with JavaScript disabled; dataset links, context, and forms remain usable.


## REF-84 explicit selection

Dataset list Records/Updated describe Current snapshot count/publication time, never the
concept. Without Current these cells show an em dash. Authorized maintainers see the secondary
Concept in progress link, which opens Versions. With REF-162, the Dataset name opens
the explicit Concept Records context when an authorized active Concept exists and
there is no Current; otherwise it retains the Current route.
No role-dependent or session-wide implicit mode is used. Explicit concept headers retain
working content timestamps/counts, and their banner states the pinned baseline and change
status. See [ADR-015](../architecture/adr-015-explicit-dataset-concepts.md).

The Dataset list's rightmost **Version** column shows `Version N` without `Current`, or
`Concept in progress` for an administrator who can see an active concept. The concept
label links to Versions and replaces the version name in that cell. No selected publication
and no visible concept displays an em dash. The Dataset-name cell contains only its name.

Published Dataset detail/Records pages show `Version N` first in the shared content
metadata line, before Record count and Updated time. The separate version/status/read-only
line is removed; snapshot authorization and concept banners retain their behavior.

Record pages now expose one system Status column (Published/Edited/Added/Deleted).
Concept result pagination includes baseline tombstones, but `context.record_count` always
counts surviving working Records. `deleted_count` is separate and dataset-wide; `page.total`
counts matching displayed rows, including tombstones. Record identifier search is explicit
per request; there is no session-wide selection. Published pages show Published only.

## REF-162 list and Configure presentation

The list routing decision uses the server's authorized context projection, without
an intermediate redirect. A visible Concept replaces the empty Version marker;
Record count and Updated retain their meaningful no-Current placeholders. The
shared Dataset-table badge link controls vertical alignment. Optional Dataset icons
use the same trusted local catalog lookup in the list and shared Dataset context header, with
Table as the empty-selection fallback and decorative SVG semantics.

Configure places the Edit dataset text link in Dataset details. It opens inline
edit mode on the same Configure page, with Save dataset and Cancel. The separate Access management tab renders the existing export
access composition inline. Versions removes its page-level Review changes and Concept actions
controls; the authorized row link and Record-page action order remain intact.

REF-162: The overview gear has tooltip `configuration` and opens
`/datasets/<key>/configure` without selecting a version. Fields is visible but
read-only there; explicit Concept/version configuration retains its Fields view.
The Dataset name link still opens the applicable Records context. Metadata editing,
ownership, access and deletion use current Dataset permissions independently of versions.
