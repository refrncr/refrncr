# Overview

REF-238 replaces the sign-in landing dataset list with an attention overview, four recently
visited datasets and four recently created readable datasets. Product decisions remain in
[Jira REF-238](https://cleverhorizons.atlassian.net/browse/REF-238).

## Service and authorization

`OverviewService` uses the existing Unit of Work, `DatasetAuthorizationService` bulk read and
maintenance decisions, version lifecycle calculation and REF-301 source identifier compatibility
check. SQL aggregation counts incomplete mappings without materializing record values. Query count
is independent of the number of attention datasets; the four recent export actions perform at most
four existing permission checks. No Cloud-specific ownership model or new public REST contract is
introduced. All reads use one coherent database snapshot.

Own datasets follow current owning-team membership. The administrator override is explicit, applies
to every count and filtered view, and is checked server-side. Direct non-admin `all_owners=1`
requests receive 403. Draft and Scheduled details are only used for maintainable datasets. Recent
cards recheck visibility and never make a previously visited dataset accessible. New unpublished
datasets are shown only to their maintainers, matching the existing dataset overview contract.

Reasons 1–5 are cards; reasons 6–8 independently count datasets in the tally. The headline counts
the union. On Overview, the most severe card reason (1–5) determines both group and callout/destination;
reasons 6–8 appear only as short labels in “+N more”. Filtered View all uses the requested reason
for both callout and destination, including the Draft mapping filter for reason 7. Unfiltered
View all uses the card reason where available, otherwise the most severe tally reason.
The compact problem text and the plain-text preparation note are separate fields. A ready Draft changes the destination but does not clear a published issue.
All version dates use the application's UTC calendar. Draft age uses the active Draft's last
mutation timestamp, not administrative ownership or export-access changes.

The missing-Current predicate uses the existing lifecycle states. Today's publication model gives
the last non-cancelled version an open-ended validity interval, so ordinary publication cannot
produce terminal expiry without a successor. REF-238 does not add an expiry operation or treat a
never-published dataset as expired.

## Private visits and upgrades

Alembic revision `0034_ref_238` follows `0033_ref_301` and creates `dataset_visits` on SQLite and
PostgreSQL. Existing installations use the normal `refrncr start` / database preparation upgrade
path. There is no backfill: an empty history is an honest initial state. The composite primary key
is `(user_id, dataset_id)`; both foreign keys cascade on physical deletion. A user/time index serves
recent reads. Downgrade removes only navigation history.

Successful authenticated HTML GETs of dataset pages record visits, including tabs, versions and
Draft pages. Redirects, denied requests, POSTs and export downloads do not record visits. The
service does not use a visit as evidence of read permission. Read permission is checked when
rendering recent cards. Each write locks the user (SQLite uses the existing serialized writer),
upserts the visit and prunes to twenty entries in one transaction. Equal clock ticks use monotonic
microsecond ordering so the just-visited dataset cannot be evicted. The history is private and is
explicitly excluded from domain audit in the audit catalog and coverage inventory.

`/` and `/attention` responses are `no-store`. `reason=1` through `reason=8` filters attention;
`all_owners=1` is carried through links and forms. The switch uses the existing auto-filter
controller with a native Apply fallback without JavaScript.

## UI components

[Component specimens](overview-styleguide.html) use the application's own CSS, fonts and icons.
Shared elements are `.dataset-card`, `.section-heading` / `.count`, static `.callout` variants and
native `.switch`. Cards are a deliberate exception for Overview as a starting point; datasets,
mappings, hierarchies and diffs remain the primary working surfaces.

The nine new tokens express sizing and semantic roles, without introducing color primitives:
`--card-grid-min-width`, `--card-padding-compact`, `--color-card-hover-border`,
`--color-count-surface`, `--color-count-text`, `--switch-track-width`, `--switch-track-height`,
`--color-switch-off`, `--color-switch-on`. Forest resolves the same aliases against its own colors.
The existing icon-tile styling is shared outside the picker; Forest uses `--color-link` for its icon.

The shared card places its icon and borderless action menu above a full-width, two-line title
and description. Description remains visible alongside attention feedback. Version/date sit on the
left of the metadata row and record count on the right; callout, preparation note and short extra
reason labels follow below. Section actions align at the right.

Overview-only rules cover section spacing, grouping/tallies, a three-column attention grid at desktop
widths, two columns from 768px, and one column below 768px. Empty recent visits show a plain sentence
and link without a zero badge. Card hover uses the existing link-color alias; card and feedback
borders retain at least 3:1 contrast in both themes.
The title's full text remains in the link's accessible name and expands on keyboard focus.
Descriptions expand on keyboard focus and remain complete in the accessibility tree. Static
callouts do not announce as live regions. Menus reuse native `details` and the existing export
macro; REF-255 remains the separate menu-improvement story.

## Verification

- `tests/test_overview.py`: ownership, admin override, horizons, mappings, counts, Draft destinations,
  personal bounded visits, query counts and rendered browser navigation.
- `tests/test_overview_migration.py`: SQLite/PostgreSQL upgrade, preserved datasets, aware timestamps
  and cascading deletion.
- `tests/browser_overview_app.py` and `tests/javascript/overview_browser.cjs`: Chrome at 375, 768
  and 1440px in Light and Forest, computed contrast, keyboard navigation and no-JavaScript fallback.

The general Concept → Draft rename and source-owner informational reason remain separate work.
