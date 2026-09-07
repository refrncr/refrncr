# Dataset context persistence (REF-130)

Dataset overview and detail context are authorized service reads. `list_dataset_overview`
resolves viewer access in bulk, then calls the Dataset port's `current_overview` once. The
Community adapter scopes both the grouped record-count subquery and dataset rows to those IDs;
only authorized all-resource access uses an unrestricted scope. Empty access skips the query.
The result combines dataset data, exact count (including zero), and an aware UTC freshness value.
There is no overview pagination today; record-page pagination remains unchanged.

`DatasetRecord` stores only the mutable current working set. Its revision is an optimistic
concurrency counter, not another row/version. The explicitly named `current_overview` contract
must continue to count only this working set when historical/publication storage is introduced.
No versioning, ownership relation, Cloud context, or external runtime dependency is added here.

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
changing freshness. Record edits retain their existing revision/audit behaviour, including saves
of unchanged values. Name, description, access grants, initial user ownership and unrelated
metadata do not touch content freshness. No ownership mutation service exists yet. Import staging,
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
audits. Older migration fixtures seed via the current application and downgrade to the historical
schema before exercising upgrade; assertions on downgraded schemas select only historical columns.

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
2. Check the Datasets → No owning team → dataset name breadcrumb. Only Datasets is linked, with
   restrained green text and no underline. Grey separators are decorative, and the final dataset
   name is current. Check wrapping with a long unbroken name and increased text size.
3. Tab through links and primary actions. Confirm logical order, visible focus, and usable table
   navigation. Confirm screen-reader breadcrumb/current-page and table-heading announcements.
4. Check contrast using existing secondary-text and link tokens. Confirm every timestamp is
   English UTC and names containing HTML-like characters display as text.
5. Repeat with JavaScript disabled; dataset links, context, and forms remain usable.
