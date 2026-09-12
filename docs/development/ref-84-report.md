# REF-84 implementation and validation report

Status: technically ready for local commit after final acceptance validation; see the final
acceptance section below. Independent GitHub CI is still required before merge.
Earlier lifecycle and presentation results below remain historical evidence.
Branch `feat/ref-84-version-diff`. Nothing staged, committed or pushed. GitHub CI remains
the independent gate before merge; no deployment or release is claimed.

## Scope and approved decisions

REF-84 belongs in Community/core. The product owner approved the shared REF-83/84
classification correction, the REF-149 review-read exception and the explicit concept
lifecycle/UI correction, including both baseline decisions, on 2026-09-11.

- Field datatype changes, additions and removals count only as Field changes. Stable
  surviving Records change only for canonical value differences in comparable surviving
  Fields. The REF-83 summary and REF-84 detail use the same classifier and baseline/revision.
- A first concept has null `base_version_id`; the UI says **No published version yet**.
  Summary and detailed review count all Fields/Records Added without a synthetic snapshot.
  Valid first publication creates Version 1, visible to ordinary users only once Current.
- Cancellation of a pinned Scheduled baseline preserves concept identity, baseline and all
  edits as **Stale**. Cancellation/staleness are atomic because staleness derives from the
  baseline cancellation marker. No rebase, new concept or snapshot occurs. Administrator
  edits remain possible, but publication/replacement are blocked pending REF-155 or a
  separate recovery story. This temporary operational lockout is intentional.
- Publication validation, immutable snapshots, numbering, effective dating, authorization,
  one Scheduled version and existing REF-83 audit/rollback rules remain authoritative.

## Initial analysis and before/after navigation

The full local diff and merged REF-83 foundation (`main` at `a3f8fed`, PR #46), working
copy/revision semantics, diff contracts, lifecycle forms, content/import/export routes,
classifier, browser fixtures, migration 0015 and preflight were inspected before editing.
The generic Concept label followed role-dependent working-copy selection. Equality alone
could not represent a deliberately started but unchanged concept. Requiring a published
baseline before any editing would prevent first publication; the approved null-baseline
rule resolves that dependency. Pinning a baseline also required the explicit stale decision.

| Area | Before | Implemented behavior |
| --- | --- | --- |
| Dataset name/link | Administrators entered working content | Current for every role; no hidden session mode |
| No Current | Potential implicit administrator working context | Safe empty page; ordinary users get non-disclosing absence |
| Dataset list | Generic Concept; concept count/time | Current snapshot count/publication time; rightmost Version column with Version N or administrator-only Concept in progress link to Versions |
| New editing round | Implicit after publication | One authoritative **+ New version** label on the authoritative concept-create operation |
| Versions | General View concept alongside Review/Publish | Selectable aligned Concept row beneath its base, with Draft/Stale and matching publication columns; no misleading overview metadata |
| Unchanged concept | Indistinguishable from no active editing round | Selectable **No changes yet**, with no Review/Publish |
| Concept context | Small working-copy text and separate Versions strip | Full-width shared banner; Versions in header actions with history icon |
| Return to Current | Role could return user to working content | View current version and ordinary breadcrumbs preserve edits and select Current |
| Record state | Only content/completeness values | Final system Status column (Published/Edited/Added/Deleted); content Status = Complete untouched |
| Publication | Working copy remained implicitly editable | Successful snapshot/audit transaction closes concept; failure retains it |
| Old mutation URL | Could edit surviving working copy | Active-concept guard in authorized service transaction; safe 409/New concept path |
| Export | Implicit administrator concept selection | Normal export uses Current; explicit administrator concept export is preserved |

## Persistent model, migration and transactions

ADR-015 defines `dataset_concepts`: Dataset primary key (at most one active row), separate
concept UUID, nullable pinned baseline, creator, created/updated timestamps and start
revision. A composite foreign key binds a non-null baseline to the same Dataset; the
supporting unique index is on `dataset_versions(dataset_id, id)`. Existing working tables
hold content. Changed/unchanged state derives from the shared classifier; absence of the
row means inactive. Migrated creator is null and displays **System (migration)**.

`DatasetVersionService.create_concept` is the single create operation, including Dataset
creation within its existing UoW. It locks and authorizes, rejects duplicates, chooses the
latest non-cancelled baseline and restores it with bounded INSERT SELECT operations when
necessary. Equal copies retain Record revisions; actual restoration advances them so old
forms cannot overwrite restored content. Concept identity, Dataset revision and audit
commit atomically. Successful publication closes the row inside the existing snapshot/audit
transaction. A Scheduled result also closes it; a following concept may bind Scheduled,
but another publication remains blocked until activation. REF-141 deletion cascades active
concept metadata and snapshots while preserving audit history.

Migration `0016_ref_84_active_concepts.py` follows unchanged migration 0015. With writers
stopped and a restorable backup, `refrncr dataset-concept-preflight` reports corrupt or
ambiguous canonical/identifier state as Dataset IDs without content. `--upgrade` repeats
preflight and installs the migration. Equal copies become inactive; divergent/unpublished
copies become active without rewriting any content, snapshots or audit. DDL, conversion
and Alembic revision roll back together; insert failure and safe retry are tested on both
engines. Older supported schemas still run unchanged 0015 first. Active concepts block
normal downgrade; restore the matching pre-upgrade backup when rollback is required.

## Shared classification, bounds and review identity

Field matching uses stable UUIDs, canonical schema properties and a deterministic longest
increasing subsequence for relative moves (O(F log F)); inserting/removing Fields does not
mark neighbours moved. Identifier components retain immutable Field-key order. Records
match UUID rather than mutable identifier; delete/recreate is Added plus Removed. Exact
REF-145 canonical bytes govern comparable surviving values, including Unicode, Decimal,
large integers and temporal types. Existing historical stored counts are not rewritten.

SQL aggregates classify Records once for summary/detail and concept badges. A concept
Record page requests change states in one scalar projection for its page IDs; no per-Record
queries or third algorithm. Removed Records also appear as read-only Deleted baseline rows in the concept table. Configure badges use
the same Field classifier and a presentation map; badges never enter accessible names.

Detailed review binds actor, Dataset, concept UUID, Dataset revision and pinned baseline
ID/number/status. Every child request reauthorizes and validates that identity in a coherent
SQLite snapshot or PostgreSQL REPEATABLE READ UoW. Writes, publication, cancellation and
Scheduled activation invalidate an open review. Publication optionally rechecks the same
review under its existing write lock; review is not an approval or mandatory gate.

Record pages stream scalar candidates, retain at most 51 entries in a bounded heap and
return 50. Search is a literal NFC/casefolded substring on old/new identifiers (200-character
limit), never SQL text or value search. Keyset anchors retain stable IDs. Field/value detail
pages are also 50 entries, with explicit pagination and nulls. Long values remain available
through escaped disclosure controls. Opening additionally streams canonical integrity rows
in batches of 100 under the Dataset writer lock. It can delay writers on large datasets.

Earlier 10,100-changed-Record measurement of the unchanged detailed-review page path on SQLite/PostgreSQL 16: **9 / 12 SELECTs** and
**228,029 / 246,486 peak traced Python bytes** per Record page, respectively. Query counts
match the 100-Record fixture; responses contain 50 entries. Tracemalloc is Python allocation
evidence, not whole-process/database RSS. Record + detail + Summary totals are **29 / 38
SELECTs**, including existing PostgreSQL introspection. Full Record graph reads are explicitly
forbidden by detail-path tests. Wide details prove complete 50/50/3 value pagination.

There is no persisted Dataset-size cap; the 10,000 import limit is per upload. Ordinary
Record tables retain the existing full selected-Record sort before slicing. Publication
still validates/materializes complete content and stores full snapshots; no constant-memory
publication or SQL-side ordinary Record pagination is claimed. History loads version metadata.

## Audit, authorization and security

REF-73 remains the sole authority: active owning-team members and Community administrators
can create/open/edit concepts. Legacy viewer/editor/export grants cannot. Current/Expired
use current permissions; ordinary users never receive concept/Scheduled/Cancelled content.
CSRF, trusted session actor, stable Dataset identity, escaping, non-disclosing denial and
serialized mutation guards are retained. No role, REST API, background job or dependency
is added. Governance stays independent of the content lifecycle.

`dataset.concept_created` commits once with trusted actor/Dataset and only bounded baseline
version number (0 for no baseline) plus concept revision. Duplicate, denied or failed creation
has no success event. Dataset creation calls the same operation, so its transaction contains
both the existing created event and concept-created event. No second audit model exists.

`dataset.version_diff_viewed` is the explicitly approved REF-149 ordinary-read exception:
only a new valid, changed review opening commits the event before returning. Its eight
context values are bounded integers: baseline number, revision and six change counts.
No identifiers, search terms, values or tokens enter context. Audit insert/commit failure
returns no review. Missing/denied/stale/no-change requests emit no success event; pagination,
search, filters, details and ordinary Current/Concept navigation emit none. Catalog,
presentation, inventory, coverage and the static guard reflect both events and exclusions.

Signed links are session/actor-bound and carry no content. Multiworker deployments must
inject a shared server signing key; default per-instance keys invalidate links on restart.
No-store responses, no-referrer review links and BFCache revalidation protect stale views.
An audit event proves server acceptance, not guaranteed browser delivery/rendering.

## UI and real Chrome evidence

No design tokens changed. Shared components: concept banner in `components/concept.html`
with feedback.css, header Versions action/history icon and existing buttons/native sidepanels.
Table variants: diff comparison table, explicit Record system Status column, muted Deleted
rows and quiet Field badges. Versions uses aligned Concept and published-version rows. Functional
changes are the approved lifecycle, selection and mutation guards described above.

The in-app browser bootstrap returned `Browser is not available: iab`; the existing external
Chrome/Playwright loopback setup was used. Chrome **152.0.7977.84** passed both suites at
**320, 768 and 1440 px**, with zero page errors. Lifecycle covers Current → Versions → New
concept → Concept row → edit → View current version → review → publish, plus duplicate
controls, real Status = Complete with Edited, direct denial and panel focus restoration.
Detailed review covers filters, old/new search, list/value pagination, long/null values,
keyboard/focus/Escape/Close, second-tab stale changes, Back/Refresh/Reload and publication.

Screenshots were inspected, including action wrapping, long names, banner prominence,
Current read-only controls, Concept row hierarchy and content/change-state separation.
The later final UI decision places system Status last; its narrow-screen scroll behavior
and accessible System header were checked in the final Chrome run described below.
Wide content/version tables scroll inside their container; no page overflow was observed.

Artifacts are outside the repository in `ref84-concept-chrome` and
`ref84-diff-chrome-lifecycle` (the selected REFRNCR_BROWSER_OUTPUT directories):

| Required view | Screenshot examples |
| --- | --- |
| Dataset list without active concept | `list-before-1440.png` |
| Dataset list with one active concept | `list-active-1440.png` |
| Current Records | `current-1440.png`, `current-768.png`, `current-320.png` |
| Concept banner, Edited and real Status value | `concept-1440.png` |
| Versions without concept | `versions-before-1440.png` |
| Versions with Concept row | `versions-active-1440.png`, `versions-active-768.png` |
| Review changes | `review-768.png`, `review-1440.png` |
| Narrow concept | `concept-320.png` |
| Detailed diff panels | `added-panel-320.png`, `changed-panel-1440.png` in diff directory |

Each suite also writes `results.json`. Browser fixtures and reproducible scripts are included;
screenshots, databases, caches and temporary scripts are not repository changes.

## Validation ledger for the lifecycle correction

Environment: Python **3.12.2**, SQLite **3.51.0**, PostgreSQL **16.15
(Debian 16.15-1.pgdg13+2)** in an isolated disposable container on loopback port 55484.
The existing host port 5432 resolved to Homebrew PostgreSQL 15.18 despite a Docker mapping.
That diagnostic broad run was stopped and is not PostgreSQL 16 evidence. The final run
uses REFRNCR_TEST_POSTGRES_URL pointing explicitly to the verified PostgreSQL 16 container.
The original pre-lifecycle REF-84 results likewise are not final evidence for this correction.

The first complete verified PostgreSQL 16 run finished with 2684 passed, 4 failed,
0 skipped and 3 warnings in 752.52 s. The four concurrent-export tests still requested
an implicit working-copy export. Their service invocation now explicitly selects concept
export; synchronization, exported-content assertions and audit assertions are unchanged.
The entire affected file then passed: 31 passed, 0 failed/skipped/warnings in 2.46 s.
A final complete run follows this correction; the ledger below records its result.

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest` (SQLite and verified PostgreSQL 16 enabled) | 2688 passed, 0 failed, 0 skipped, 3 warnings; 537.45 s (8m57s) |
| `.venv/bin/python -m pytest -q -s tests/test_dataset_diff.py -k large_projection` | 2 passed, 118 deselected, 0 failed/skipped/warnings; 161.81 s |
| `.venv/bin/python -m pytest -q tests/test_dataset_export.py` | 31 passed, 0 failed/skipped/warnings; 2.46 s |
| `.venv/bin/python -m pytest -q tests/test_audit_catalog.py` | 55 passed, 0 failed/skipped/warnings; 26.19 s |
| `.venv/bin/python -m ruff check .` | Passed |
| `.venv/bin/python -m ruff format --check .` | Passed, 153 files formatted |
| `.venv/bin/python -m pre_commit run --all-files` | Passed after one whitespace correction |
| `.venv/bin/python -m pre_commit run --files <each new untracked file>` | Passed; YAML/TOML hooks have no matching new files |
| `node --check <each static JS and test CJS file>` | 22 passed |
| `git diff --check` | Passed |
| `node tests/javascript/concept_browser.cjs` | 3 viewports passed; zero page errors |
| `node tests/javascript/dataset_diff_browser.cjs` | 3 viewports passed; zero page errors |

The three full-suite warnings are existing deprecations: one Starlette TestClient/httpx
warning and two Python 3.12 SQLite datetime-adapter warnings in the historical REF-75
upgrade fixture. No warnings are suppressed and no dependency changes were introduced.

The database test commands above use this exact disposable test configuration (the
credentials are the repository's existing test-only values):

```sh
REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test .venv/bin/python -m pytest
REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test .venv/bin/python -m pytest -q tests/test_dataset_export.py
REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test .venv/bin/python -m pytest -q -s tests/test_dataset_diff.py -k large_projection
```

Chrome commands use the existing external REFRNCR_PLAYWRIGHT_MODULE and
REFRNCR_BROWSER_EXECUTABLE settings plus the output directory; fixture commands are
`.venv/bin/python tests/browser_concept_app.py` (5185) and
`.venv/bin/python tests/browser_dataset_diff_app.py` (5184). These bind disposable loopback only.

New tests cover first/no-change concepts, duplicate/concurrent creation, active-state gates
for every content writer, authority/CSRF/IDOR, no-change/no-event, publish closure/rollback,
cancel/staleness rollback, snapshot restoration, Current/concept export, paged badges,
preflight ambiguity/corruption, equal/divergent/unpublished migration, DDL/copy rollback,
CLI and retry. Existing REF-83/84, CRUD, import/export, context/browser, governance, deletion,
auditing and historical migration tests were adapted explicitly to the approved lifecycle;
production guards and historical migration checks were not bypassed for green tests.

## Documentation and impact review

Updated ADR-014 and new ADR-015, Dataset versions/context/governance, UI foundation, audit
policy/catalog/inventory/coverage, migration/preflight guidance and REF-83/84 reports.
Confluence **API Architecture & Effective Dating**, page 41943041, was updated from v5 to
**v7** with pinned concepts, Current-first navigation, first publication, Stale cancellation,
0016 transition, bounded audit and the explicit operational recovery limitation. Existing
planned API guidance was preserved. Version 6 records the approved correction; version 7
records the final local validation. No Jira comments/status changes were sent.

Authorization/security, schema/migration, trusted service contracts, docs and architecture
are affected and reviewed. No Cloud tenancy, new dependencies, REST API endpoint, background
worker, second classifier/publication path or Discard implementation. Migration 0015 is
unchanged. Only the approved REF-84 work and lifecycle correction are included.

Remaining operational limits: a Stale concept cannot be published/replaced until recovery;
large synchronous integrity/publication work can hold locks; ordinary Record sorting and
full snapshots retain their documented memory/storage costs. Native screen-reader software
was not run; semantic structure, keyboard and focus were checked in real Chrome. GitHub CI
cannot validate this unpushed change yet and must pass before merge.

## Explicit proposed staging and handoff

The following is a recommendation only; no staging, commit or push was performed. This
list also identifies every changed/new implementation, test and documentation file.

```sh
git add -- \
  docs/architecture/adr-014-dataset-versions.md \
  docs/architecture/adr-015-explicit-dataset-concepts.md \
  docs/development/audit-coverage.json \
  docs/development/audit-inventory.json \
  docs/development/audit-policy.md \
  docs/development/dataset-context.md \
  docs/development/dataset-governance.md \
  docs/development/dataset-versions.md \
  docs/development/ref-83-report.md \
  docs/development/ref-84-report.md \
  docs/development/ui-foundation.md \
  src/refrncr/audit.py \
  src/refrncr/audit_guard.py \
  src/refrncr/audit_presentation.py \
  src/refrncr/cli.py \
  src/refrncr/concept_contracts.py \
  src/refrncr/concept_persistence.py \
  src/refrncr/concept_upgrade.py \
  src/refrncr/dataset_diff.py \
  src/refrncr/dataset_versions.py \
  src/refrncr/diff_classification.py \
  src/refrncr/diff_contracts.py \
  src/refrncr/diff_persistence.py \
  src/refrncr/migrations/versions/0016_ref_84_active_concepts.py \
  src/refrncr/models.py \
  src/refrncr/persistence.py \
  src/refrncr/ports.py \
  src/refrncr/record_import.py \
  src/refrncr/review_tokens.py \
  src/refrncr/services.py \
  src/refrncr/static/css/components/cards-badges.css \
  src/refrncr/static/css/components/feedback.css \
  src/refrncr/static/css/components/tables.css \
  src/refrncr/static/css/layouts/app-shell.css \
  src/refrncr/static/js/refrncr.js \
  src/refrncr/static/js/version-diff.js \
  src/refrncr/templates/base.html \
  src/refrncr/templates/components.html \
  src/refrncr/templates/components/concept.html \
  src/refrncr/templates/components/icons.html \
  src/refrncr/templates/components/version_diff.html \
  src/refrncr/templates/pages/datasets/_record_panels.html \
  src/refrncr/templates/pages/datasets/_record_table.html \
  src/refrncr/templates/pages/datasets/concept_required.html \
  src/refrncr/templates/pages/datasets/concepts/_banner.html \
  src/refrncr/templates/pages/datasets/concepts/_version_row.html \
  src/refrncr/templates/pages/datasets/detail.html \
  src/refrncr/templates/pages/datasets/edit.html \
  src/refrncr/templates/pages/datasets/fields/create.html \
  src/refrncr/templates/pages/datasets/fields/edit.html \
  src/refrncr/templates/pages/datasets/import/wizard.html \
  src/refrncr/templates/pages/datasets/list.html \
  src/refrncr/templates/pages/datasets/no_current.html \
  src/refrncr/templates/pages/datasets/records.html \
  src/refrncr/templates/pages/datasets/records/delete.html \
  src/refrncr/templates/pages/datasets/records/form.html \
  src/refrncr/templates/pages/datasets/versions/_navigation.html \
  src/refrncr/templates/pages/datasets/versions/diff_detail.html \
  src/refrncr/templates/pages/datasets/versions/list.html \
  src/refrncr/templates/pages/datasets/versions/publish.html \
  src/refrncr/templates/pages/datasets/versions/review.html \
  src/refrncr/templates/pages/datasets/versions/review_error.html \
  src/refrncr/version_contracts.py \
  src/refrncr/version_persistence.py \
  src/refrncr/version_upgrade.py \
  src/refrncr/web.py \
  tests/browser_concept_app.py \
  tests/browser_dataset_diff_app.py \
  tests/browser_dataset_versions_app.py \
  tests/dataset_helpers.py \
  tests/javascript/concept_browser.cjs \
  tests/javascript/dataset_diff_browser.cjs \
  tests/javascript/dataset_versions_browser.cjs \
  tests/javascript/side_panels.cjs \
  tests/javascript/version_diff.cjs \
  tests/test_audit_catalog.py \
  tests/test_audit_coverage.py \
  tests/test_audit_history.py \
  tests/test_concept_lifecycle.py \
  tests/test_concept_migration.py \
  tests/test_configure_overview.py \
  tests/test_dataset_access.py \
  tests/test_dataset_browser.py \
  tests/test_dataset_context.py \
  tests/test_dataset_deletion.py \
  tests/test_dataset_diff.py \
  tests/test_dataset_diff_views.py \
  tests/test_dataset_export.py \
  tests/test_dataset_foundation.py \
  tests/test_dataset_governance.py \
  tests/test_dataset_governance_review.py \
  tests/test_dataset_records.py \
  tests/test_dataset_version_migration.py \
  tests/test_dataset_version_views.py \
  tests/test_dataset_versions.py \
  tests/test_field_datatype_migration.py \
  tests/test_field_datatype_review.py \
  tests/test_field_datatype_services.py \
  tests/test_migrations.py \
  tests/test_record_system_status.py \
  tests/test_side_panel_javascript.py \
  tests/test_table_rows.py
```

Suggested Conventional Commit and push (not executed):

```sh
git commit -m "feat: add explicit concept lifecycle and version diff (REF-84)"
git push -u origin feat/ref-84-version-diff
```

## Follow-up: Dataset list Version column

The product owner's subsequent visual instruction moves version presentation into a
rightmost **Version** column. Cells show `Version N` without `Current`, or
`Concept in progress` when the administrator can see an active concept. The latter
remains a separate link to Versions. With neither selected publication nor visible concept,
the cell shows an em dash. Dataset names have no version subtitle and still open Current.
This is a page-specific template presentation change using the existing table component;
no tokens, global components, CSS variants, service behavior or authorization changed.

The browser/list tests assert five semantic cells, the separate concept link and the clean
Dataset-name cell. Real Chrome 152.0.7977.84 passed the existing lifecycle suite at
320/768/1440 with no page errors; list screenshots in `ref84-version-column-chrome` were
visually inspected. The wide table retains internal scrolling at narrow widths.

Follow-up validation: `.venv/bin/python -m pytest` ran the complete local suite with
1891 passed, 796 PostgreSQL skips and 3 existing warnings in 257.44 s, finding one
remaining context-test expectation for the removed `No current version` list label.
After updating that expectation, `.venv/bin/python -m pytest -q
tests/test_dataset_browser.py tests/test_table_rows.py tests/test_dataset_context.py`
passed: **160 passed, 12 PostgreSQL skips, 0 failed, 0 warnings in 23.53 s**. The
unchanged remainder was not rerun. PostgreSQL was not enabled for this template-only
follow-up; the preceding full PostgreSQL 16 result above remains separate evidence.
Pre-commit, Ruff and `git diff --check` passed. Nothing was staged, committed or pushed.

## Follow-up: version in header metadata

The shared `content_metadata` macro accepts an optional version number. Published
Dataset detail and Records pages show `Version N` before Record count and Updated time.
The separate `Version N · Current · Read only` line is removed. Concept banners and
snapshot access remain unchanged. No design tokens or CSS variants changed.
Targeted version-view/context tests passed (38 passed, 36 PostgreSQL skips, no failures
or warnings in 7.12 s). Real Chrome 152.0.7977.84 passed 320/768/1440 with no page errors;
`current-1440.png` in `ref84-header-chrome` was inspected.

Full local header-follow-up validation: `.venv/bin/python -m pytest` completed with
**1892 passed, 796 PostgreSQL skips, 0 failed and 3 existing deprecation warnings in
240.63 s**. PostgreSQL was not enabled for this presentation-only follow-up. Ruff,
pre-commit and `git diff --check` passed; nothing was staged, committed or pushed.

## Final UI decision: system Status and retained Deleted Records

This section supersedes the earlier Change/Validation and diff-only removal presentation.
The approved UI now uses exactly Published, Edited, Added and Deleted. The last named
Record-table column is Status, with a visible System sublabel, accessible name
`Status (application metadata)`, interface typography and a column boundary. Dataset Fields
named Status, Change or Validation remain ordinary content. The green Complete application
badge is gone; content such as `Status = Complete` remains unchanged. Published uses a green
success badge, Edited/Added info badges, and Deleted a text badge on a muted row.

`VersionRead` adds deleted_count, baseline_fields, record_labels and search. The existing
record_states mapping presents the four UI statuses. REF-83/84 `_records` remains the sole
classifier: Changed → Edited, Removed → Deleted, Added → Added; unchanged survivors →
Published. Published snapshots render Published. `removed_records` selects Removed IDs
through that same SQL projection, then streams only those baseline Records/values through
the existing snapshot decoder. No new comparison algorithm, persistence row, migration,
REST API, role, dependency or background job was added. Migration 0015 remains unchanged.

The authorized read UoW combines live and baseline-deleted rows before sorting/filtering/
slicing. Each row uses its source identifier fields; datatype tags make mixed old/new types
comparable and UUIDs break ties, including delete/recreate of the same identifier. Search
is a literal NFC/casefold identifier substring, capped at 200 characters; Deleted uses its
old identifier. Search is preserved in pagination URLs. Record count remains the number
of surviving concept Records; `24 records · 1 marked for deletion` is distinct from result
pagination, whose total includes all matching live/Deleted rows. Search never changes the
Dataset-wide surviving/deleted counts.

Deleted cells match surviving Field UUIDs and compatible datatypes, avoiding collisions
when a Field key is reused. Their primary label remains the old identifier. Their detail
panel renders every original baseline Field/value using its original datatype, including
Fields removed or changed in the concept. It explains publication-time removal, has no
edit/delete controls or forms, and preserves native keyboard, Escape and opener focus.
Direct mutation requests remain rejected by the authoritative service when the live Record
is absent. Current/historical snapshots never expose concept tombstone state. Publication
stores surviving content only and closes the concept atomically, removing tombstone rows.

No audit event is added for table/search/pagination/detail reads. The existing REF-149
explicit detailed-review exception remains unchanged. Audit coverage now references the
new snapshot/audit-preservation test for ordinary concept reads. All old authorization,
CSRF and non-disclosing rules remain in force.

The ordinary Record page retains its prior full-result sorting/materialization behavior;
including tombstones extends that working set. Query count is constant with Record count,
not query cost or total memory: a both-engine test compares one versus 60 tombstones and
proves stable query count, deterministic 50/10 pagination and search. No constant-memory
claim is made for ordinary tables. Detailed diff keeps its independent bounded projection
pipeline and still uses the same classification rules.

Tests added in `tests/test_record_system_status.py` cover content/system name conflicts,
all four statuses, old-field snapshots across datatype/key reuse, search, read-only service
and HTTP mutation denial, ordinary-user denial, unchanged audit/content snapshots, combined
pagination/query counts, first-concept Added, field-only Published and delete/recreate ties.
Existing lifecycle, Record-view, table and detailed-review navigation tests were updated.
Browser fixtures include Status/Change/Validation content Fields and all four states.

Final targeted run: `REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test .venv/bin/python -m pytest -q tests/test_record_system_status.py`
→ **10 passed, 0 failed/skipped/warnings in 5.88 s**, SQLite and verified PostgreSQL 16.15.
An earlier targeted combination ran 133 tests; two failures came from creating a test
login session after capturing the expected database snapshot, and were corrected by
creating both sessions before that snapshot. Assertions for unchanged state were retained.

Real Chrome 152.0.7977.84: both `concept_browser.cjs` and `dataset_diff_browser.cjs` pass
at 320/768/1440, zero page errors and non-disclosing denial. The lifecycle script covers
all statuses, deletion/detail, old-identifier search/reset, keyboard/focus, publishing and
post-publication disappearance. It also scrolls the System column into view at narrow
widths. Screenshots were visually inspected, including concept-1440, deleted-panel-320 and
system-status-320. The screenshot helper waits for panel animations to finish. An initial
search-reset click timed out; the native reset was reproduced successfully, and the final
script explicitly exercises Enter submission and verifies restoration of all four rows.
Artifacts: `ref84-status-chrome-final` and `ref84-status-diff-chrome`, outside the repository.

Full final SQLite/PostgreSQL 16 suite: **2698 passed, 0 failed, 0 skipped, 3 existing
deprecation warnings in 524.54 s (8m44s)**. Command:
`REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test .venv/bin/python -m pytest`.
Environment: Python 3.12.2, SQLite 3.51.0, PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2).
Warnings remain one Starlette/httpx deprecation and two historical SQLite datetime-adapter
warnings. No warnings were suppressed. Pre-commit (tracked and all new files), Ruff lint,
Ruff format (154 files), 22 JavaScript syntax checks and `git diff --check` passed.
Confluence API Architecture & Effective Dating (page 41943041) updated **v7 → v8**:
final system Status/tombstones, combined ordering/counts, read-only/audit boundaries,
prior UI refinements and final validation. Existing planned API guidance was preserved.
No staging, commit or push is authorized or performed; GitHub CI remains required before
merge. The ordinary-table full-result memory/sort cost remains the principal scaling limit.

The first full status run found **3 failures, 2695 passed, 0 skipped, 3 warnings in
726.10 s**. Two tests expected the previous no-search/Incomplete interface. The third
caught a real ordering regression: datatype tags sorted missing identifier components
before present ones within incomplete Records. The key now retains the existing null-last
flag before datatype and value; UUID tie-breaking is unchanged. The original deterministic
legacy-ordering assertion was retained. Updated affected tests then passed with
`REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test .venv/bin/python -m pytest -q tests/test_dataset_browser.py tests/test_dataset_records.py tests/test_record_system_status.py`:
**186 passed, 0 failed/skipped, 2 existing SQLite deprecation warnings in 26.84 s**.
The full final rerun above includes this fix. Audit catalog/coverage guard separately passed
**55 tests in 21.62 s**, after keeping the coverage exclusion rationale identical to the
central catalog and recording the extra tombstone behavior in its success description.

## Final acceptance review (2026-09-11)

This section supersedes earlier intermediate UI choices and validation ledgers. The complete
REF-84 Jira description was reread. `git fetch origin main` succeeded; HEAD and current
origin/main both resolve to `a3f8fed27f2b34e59d10a61504ab4bf7f96a2563`. Work remains on
`feat/ref-84-version-diff`. The explicit staging recommendation above covers all 102
changed/new files, including the separately requested shared app-shell adjustment. The
index is empty. No commit, push, Jira transition, deployment or CI result is claimed.

Part 1 supplies the shared classification, revision-bound on-demand query service,
authorization and bounded audit exception. Part 2 supplies Summary/Fields/Records,
search/filter/pagination, read-only sidepanels and the existing publication flow. The
approved explicit-concept lifecycle and migration 0016 remain part of this branch;
ADR-015 records that persisted lifecycle decision, not an extra ADR for diff presentation.

Final corrections found and implemented during this review:

- **Review changes** is restored on Versions under the existing `can_review` rule, as
  required by the latest instruction. Concept pages retain their review entrypoint.
- The existing Publish panel also offers optional review for a first concept without a
  published baseline. Direct first publication without opening detailed review still works.
- Comparable Record values must belong to a surviving Field with the same canonical
  datatype. The shared SQL classifier and Changed-value projection now both enforce this.
  Retyping a Field, including replacing its values, remains a Field change; a separate edit
  in an unchanged-type Field remains one Record change. Summary, diff and status agree.
- Versions no longer performs unused REF-83 publication preparation. Its removed Publish
  action had left an unnecessary full snapshot materialization behind. Publication validation
  remains in the existing Publish panel/service. Audit inventory and coverage reflect this
  removed call; a regression fails if Versions tries to materialize a publication snapshot.
- Main concept Records/configuration now open Publish in the same REF-83 sidepanel.
  The restored REF-83 browser suite caught the missing panel binding. One shared banner
  variant supplies a single host; links inside existing edit panels retain the full-page
  fallback so they do not introduce nested dialogs. The review's panel remains unchanged.
- Published now uses the shared green success badge. The empty app top bar is removed,
  with the navigation opener in the content area for collapsed/mobile navigation. The labelled
  Settings header remains. No new design token, dependency or page-specific CSS was added.

Publication from review passes the session-bound review identity in the existing URL and
the exact expected revision in the unchanged REF-83 form. The existing service rechecks
revision, concept identity and baseline under its write lock. A second-tab mutation after
opening the Publish panel blocks POST and requires Reload review. There is no persisted
review-completed flag, mandatory review gate, alternative publisher or new permission role.

Websecurity evidence includes POST/PUT/PATCH/DELETE rejection on review routes; unchanged
persisted content/audit/session snapshots for child GETs; required publication CSRF;
escaped search and content; duplicate/unknown/oversized query rejection; search maximum
200, query maximum 4096 bytes, bounded signed token and positive detail-page bounds;
non-disclosing role/IDOR/session-token denial; generic failure responses; and no-store plus
no-referrer for revision-bound pages and details. GET never changes Dataset content. The
one successful new-review audit insert is the explicit, approved REF-149 read exception.
Denied, missing, stale and rolled-back reviews never produce its success event. Existing
session-cookie and response-security regressions run in the full suite.

Additional tests in this acceptance pass are
`test_datatype_replacement_is_only_a_field_change`,
`test_first_publication_panel_offers_optional_review`,
`test_versions_review_entry_does_not_materialize_publication`,
`test_review_websecurity_methods_parameters_and_escaping`, and
`test_review_failure_hides_internal_details`. All use the existing SQLite/PostgreSQL fixture.
The Chrome script now explicitly enters review from Versions by keyboard and submits a
stale publication form after a second-tab mutation. No assertions or regressions were
removed or weakened to obtain a pass. One initial Chrome attempt reached a full-page
detail fallback before deferred JavaScript loaded; waiting for the page load event before
keyboard activation fixes test readiness without a sleep. One formatting check initially
requested wrapping normalization; the existing formatter applied it.

The existing REF-83 browser fixture and script were also updated for explicit concept
creation, direct version-to-Records links and publication from concept pages. Their original
publication, validation, cancellation, numbering, snapshot download, import and no-JavaScript
assertions remain. The Concept row is asserted by its accessible link, not CSS uppercase.
After partial mutation runs the fixture is reset to a fresh in-memory database. The final
panel-template adjustment is additionally covered by a complete 40-test web rerun on both
engines and the actual REF-83 Chrome workflow; the full suite below runs alongside it.

The bounded projection benchmark was rerun with 100 and then 10,100 matching Records:

| Database | SELECTs per Records page | Peak traced Python bytes | Returned rows |
| --- | ---: | ---: | ---: |
| SQLite 3.51.0 | 9 | 228408 | 50 |
| PostgreSQL 16.15 | 12 | 249692 | 50 |

The SELECT counts are identical at both sizes; both tests passed in 92.05 s. This measures
Python allocations, not total RSS or database memory. Scalar streams fetch 100 rows per
batch; the heap retains at most 51 candidates, with no Record/value graph. Value details
use SQL LIMIT 50. Existing snapshot/working-table composite indexes support the joins;
no new diff index or cache is introduced. Canonical validation at review opening is linear
and serialized; page search/order scans identifiers, so bounded memory is not constant CPU.

The full database run enables PostgreSQL rather than skipping its parametrized cases.
It runs the entire repository collection: SQLite and PostgreSQL 16 cases together,
including migrations/preflight, CLI/startup, REF-83 lifecycle, CRUD/import/export/deletion,
REF-73 authorization, audit history/coverage, JavaScript controller tests and security.
The earlier acceptance run was deliberately interrupted after the Versions cleanup was
identified; it is not counted as completed validation. The following final run covers that
cleanup too. Final result: **2708 passed, 0 failed, 0 skipped, 3 existing warnings in
658.89 s (10m58s)**. Python 3.12.2; SQLite 3.51.0; PostgreSQL 16.15
(Debian 16.15-1.pgdg13+2, aarch64). The warnings are one Starlette TestClient/httpx
deprecation and two Python 3.12 SQLite datetime-adapter warnings in the historical REF-75
fixture. None are suppressed. JUnit independently records zero failures/errors/skips.

Validation commands use the disposable PostgreSQL test URL (repository test-only
credentials) and the repository `.venv`. Both database fixture variants are enabled in
every database test command below:

```sh
export REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test
.venv/bin/python -m pytest --junitxml=/private/tmp/ref84-acceptance-pytest.xml
.venv/bin/python -m pytest -q tests/test_dataset_diff_views.py tests/test_dataset_diff.py -k 'not large_projection'
.venv/bin/python -m pytest -q -s tests/test_dataset_diff.py -k large_projection
.venv/bin/python -m pytest -q tests/test_dataset_diff_views.py tests/test_audit_catalog.py
.venv/bin/python -m pytest -q tests/test_dataset_diff_views.py
.venv/bin/python -m pytest -q tests/test_table_rows.py tests/test_navigation_javascript.py tests/test_side_panel_javascript.py
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
git diff --check
```

New files are additionally passed explicitly to pre-commit using
`git ls-files --others --exclude-standard -z`, decoded into separate subprocess arguments
for `.venv/bin/python -m pre_commit run --files`. This avoids overlooking untracked work.
YAML/TOML hooks have no matching files in that new-file-only invocation; no pytest cases
are skipped. JavaScript syntax is checked with `node --check` for each of all 22 `.js`
files in `src/refrncr/static/js` and `.cjs` files in `tests/javascript`.

Browser commands use externally available Playwright and installed Google Chrome through
`REFRNCR_PLAYWRIGHT_MODULE` and `REFRNCR_BROWSER_EXECUTABLE`; no repository dependency is
added. Each output directory is outside the repository, selected with
`REFRNCR_BROWSER_OUTPUT`. Exact fixtures and scripts:

```sh
.venv/bin/python tests/browser_dataset_diff_app.py
node tests/javascript/dataset_diff_browser.cjs
.venv/bin/python tests/browser_concept_app.py
node tests/javascript/concept_browser.cjs
.venv/bin/python tests/browser_dataset_versions_app.py
node tests/javascript/dataset_versions_browser.cjs
REFRNCR_BROWSER_URL=http://127.0.0.1:5185 REFRNCR_BROWSER_EMAIL=admin@example.com REFRNCR_BROWSER_PASSWORD='correct horse battery staple' node tests/javascript/navigation_browser.cjs
```

The fixture ports are 5184, 5185 and 5183 respectively, all loopback/in-memory. Browser
152.0.7977.84 runs in light mode; the main suites cover widths 320/768/1440, height 950.
The navigation regression additionally covers 240/767/1023/1024 boundaries, desktop 1440,
touch, reduced motion, Settings, containment and restoration. The browser skill was read
and its setup attempted; the in-app browser returned unavailable, so existing real Chrome
regressions were used. Final screenshot samples were visually inspected, including mobile
Changed details, desktop Summary and the concept-originated REF-83 Publish panel.

Completed focused results: shared review tests **154 passed, 0 failed/skipped/warnings,
2 deselected, 46.81 s**; additional websecurity **38 passed, 0 failed/skipped/warnings,
17.95 s**; final Versions/web/audit guard **95 passed, 0 failed/skipped/warnings, 31.45 s**;
final panel web rerun **40 passed, 0 failed/skipped/warnings, 18.95 s**; table/navigation/
controller tests **12 passed, 0 failed/skipped/warnings, 2.28 s**; performance **2 passed,
0 failed/skipped/warnings, 120 deselected, 92.05 s**. The intentionally interrupted earlier
full command used `--junitxml=/private/tmp/ref84-final-pytest.xml` and stopped at **1389
passed, 0 failed/skipped, 1 existing warning, 330.77 s**; this is partial evidence only.

All four Chrome scripts pass: detailed diff (3 viewport scenarios), concept lifecycle
(3 viewport scenarios), REF-83 (7 scenario groups, including no-JavaScript publication)
and shared navigation. No page JavaScript errors; ordinary-user review is 404 without an
entrypoint. Report artifacts are `ref84-acceptance-diff-chrome`,
`ref84-acceptance-concept-chrome`, `ref84-acceptance-versions-chrome` and
`ref84-final-navigation-chrome`, outside the repository. Earlier failed browser attempts
are not counted as passes; the final REF-83 run uses a fresh fixture after partial test
publications. No sleeps, skipped cases or weakened assertions were introduced.

The completed full-suite JUnit ledger includes 122 REF-84 service tests, 40 review web
tests, 104 REF-83 service tests, 48 version-view tests, 54 version-migration tests,
36 concept-lifecycle tests, 16 concept-migration tests, 10 Record-status tests,
21 general migration tests, 55 audit-catalog tests, 44 CLI tests, 5 packaged-CLI tests
and 2 shared panel/history-controller tests; all passed. Remaining repository tests are
included in the same 2708-pass total, rather than omitted by a selection expression.

Final quality checks passed: Ruff lint, Ruff format (154 files), all configured pre-commit
hooks over tracked files and the additional new-file check, 22 JavaScript syntax checks,
and `git diff --check`. No historical migration file or dependency manifest changed.
The final Git status is 71 modified tracked files plus 31 new files (102 total), all
unstaged; the explicit proposed staging list has no missing or additional entries.
Manual diff/path/debug/credential-pattern review found no unexpected generated files,
personal filesystem paths or real credentials. This is not a claim of an unconfigured
dedicated security scanner. Disposable browser fixtures and PostgreSQL 16 container were
stopped after validation; the user application was not stopped or restarted.

Confluence **API Architecture & Effective Dating** (page 41943041) was updated from
version 8 to **version 9**, preserving its planned API guidance and recording the final
classification, publication entrypoints, measured bounds and acceptance results. No Jira
comment or status transition was sent. REF-84 is technically ready for a local commit
within the documented scope and limits; independent GitHub CI is still required before
merge. Nothing was staged, committed or pushed. The explicit `git add` list, Conventional
Commit suggestion and push command above remain recommendations only.

Remaining limits: ordinary Record tables still sort/materialize their selected Records,
including tombstones, and REF-83 publication validates a complete snapshot. These are
documented existing paths, separate from the bounded on-demand diff; no constant-memory
claim is made for them. There is no total persisted Dataset-size cap. A cancelled-basis
concept preserves its data but cannot be published/replaced until REF-155 recovery work.
Multiworker review links require a shared injected signing key. Native screen-reader
software and independent GitHub CI are not claimed; actual Chrome keyboard/focus and
semantic markup are checked. CI must pass before merge.

Out of scope remains arbitrary historical comparisons, approval workflows, editor/reviewer
roles, mutation inside diff, persisted diff/cache, background workers, diff downloads,
REST APIs, Cloud features, dark mode and recovery/rebase/discard. Historical migration
files, snapshot shape, publication numbering/effective intervals and permission evaluation
are unchanged. Historical migration helpers preserve their original first-snapshot counts;
0016 is the previously approved additive lifecycle migration. No personal paths, real
credentials, debug code, temporary databases or browser artifacts are staged or added to
the repository; test-only credentials and attack strings are intentional fixtures.

## Independent visual-refresh review corrections — 2026-09-12

This section records the five explicitly requested corrections on
`feat/ui-visual-refresh`. Earlier sections describe the original REF-84 delivery;
the results below apply to the visual-refresh branch and supersede its contradictory
Published presentation guidance. No second review report is introduced.

Review baseline: freshly fetched `origin/main`,
`cc0ba895da610e1cb1e41aa138e4c8a31632a43a`. Starting HEAD:
`a346306fc62df945b7f3211189145485f6c4ad67`; the working tree was clean.
The baseline browser copy was compared byte-for-byte with `git archive origin/main`:
282 files, zero differences. The complete branch diff and the correction diff were
reviewed separately. Existing visual-refresh work is preserved.

### Corrections and scope

1. The shared large-report dialog now clips its own overflow and passes its bounded
   maximum height to the inner flex layout. Only the body scrolls. The fixed heading
   and Close button remain in view; short reports retain natural height. Existing
   native modality, Escape, focus containment and restoration are reused.
2. The shared Record table always renders the application-metadata Status column.
   Published uses the existing green `badge-published`, including concept, Current
   and historical tables. Edited/Added/Deleted retain their existing badge variants.
   The System sublabel, accessible column name and column boundary distinguish a
   Dataset Field named Status. UUID association, REF-83/84 classification, queries,
   tombstones and exports are unchanged. The regression verifies exported content
   Status values remain `Complete`, without adding application metadata to CSV.
3. The mobile Users variant gives the avatar and name separate grid tracks. The name
   can shrink and break anywhere within the existing chevron reservation. This rule
   only applies below 768 px; desktop table layout is unchanged.
4. The REF-84 browser test locates the Version 2 link within its semantic row header,
   then asserts that the same row is Current. No assertion depends on CSS uppercase.
   All later viewport/publication/stale/denied scenarios execute.
5. Login no longer promises administrator password resets. Its unused help-text CSS
   rule is removed. No reset functionality was added.

No tokens, dependencies, services, routes, permissions, CSRF handling, audit behavior,
transaction boundaries, database schema, migrations or API contracts changed in this
correction. No third classification algorithm was introduced. Component variants are
large report dialogs and mobile Users rows; Record status restores the existing
product contract, and login wording is page-specific.

### Files changed by this correction

- `src/refrncr/static/css/components/dialog.css`
- `src/refrncr/static/css/components/tables.css`
- `src/refrncr/static/css/layouts/auth.css`
- `src/refrncr/templates/pages/auth/login.html`
- `src/refrncr/templates/pages/datasets/_record_table.html`
- `tests/test_record_system_status.py`
- `tests/browser_ui_refresh_app.py` (new disposable fixture)
- `tests/javascript/ui_refresh_browser.cjs` (new real-Chrome regression)
- `tests/javascript/concept_browser.cjs`
- `tests/javascript/dataset_diff_browser.cjs`
- `docs/development/ui-foundation.md`
- `docs/development/ref-84-report.md`

The new browser fixture contains independent one-error and 100-error imports at each
width, plus the existing long-name Users fixture. The browser test uploads actual CSV,
opens the resulting report with the keyboard, verifies PageDown scrolling, waits for
the native scrollend event, then scrolls to the bottom with a wheel event. It checks
fixed header geometry, traps focus in both Tab directions, closes with Close and Escape, and verifies
opener restoration after the native close event. It measures rendered text rectangles
against the chevron, document/table widths, and compact normal-name rows. It also opens
Users details using Enter and a click on the email cell. No fixed sleeps are used.

The concept browser regression covers all four statuses, green Published in Current
before and after publication and Version 1 history, content Status versus metadata
Status, tombstones, old-identifier search, publication and denied access. Server tests
exercise the same status presentation and export distinction on both database engines.

### Browser evidence

Actual browser: Google Chrome **152.0.7977.84**, headless, light mode. Report/Users
viewports: **320 × 900, 768 × 900, 1440 × 900**. Concept/diff scripts retain their own
950 px height and all three requested widths. The in-app browser backend was unavailable;
the existing external Playwright convention was used with the installed Google Chrome
executable, without adding a repository dependency.

| Width | 100-error dialog height | Scroll body visible/content height | One-error dialog height | Users scroll container visible/content width |
| --- | ---: | ---: | ---: | ---: |
| 320 | 884 px | 819 / 17,190 px | 347.78 px | 286 / 286 px |
| 768 | 765 px | 684 / 12,443 px | 308.39 px | 705 / 1,025 px |
| 1440 | 765 px | 684 / 12,443 px | 308.39 px | 1,102 / 1,102 px |

In each long report, dialog `scrollHeight == clientHeight`; the body alone scrolls.
The heading rectangle is identical before and after scrolling to the final error.
Both close mechanisms restore focus. Short reports have no body scrolling. At 320 px,
the longest name ends at x=254.39 and the chevron starts at x=271: no overlap. No viewport
has horizontal document overflow. At 768 px the pre-existing desktop table scrolls
inside its container; the mobile layout begins below 768 px. At 1440 px it fits without
scrolling. Normal-name rows remain under 100 px high; desktop styling is untouched.

Screenshots and JSON measurements were generated outside the repository, including
`ui_refresh/report-100-{width}.png`, `report-1-{width}.png`, `users-{width}.png`, and
`concept/published-status-{width}.png`, `historical-status-{width}.png`. Visual inspection
confirmed the fixed report heading, compact short report, wrapped mobile name and
separate green Published column. No screenshots or temporary databases are added here.

Final browser results: **8 passed scripts, 5 known baseline failures, 0 skipped**.
Passed: `ui_refresh`, `dataset_diff`, `concept`, `dataset_versions`, `audit_history`,
`navigation`, `export_access`, `audit_export` (all `tests/javascript/*_browser.cjs`).
The diff result explicitly includes all widths, entrypoints, Summary/Fields/Records,
filters, identifier search, pagination, long/null values, keyboard/focus/Escape,
read-only GET, stale second-tab review, Back/Refresh/Reload, rejected stale publication,
successful publication and denied-user 404. Successful scripts report no page errors.

### Existing browser technical debt — reconfirmed on origin/main

Each script was rerun with a fresh disposable fixture on both current branch and the
verified origin/main copy. Each fails at the same assertion/locator on both. No test
was changed to conceal these failures, and none is counted as passing.

| Script | Existing failure on both branches |
| --- | --- |
| `table_rows_browser.cjs:59` | Dataset list expects 4 columns; the table has 5. |
| `field_datatypes_browser.cjs:34` | Add field locator targets a read-only Current page. |
| `dataset_creation_browser.cjs:46` | Expects the Dataset detail URL after creation; creation now opens the concept URL. |
| `dataset_governance_browser.cjs:31` | Edit dataset locator targets a read-only Current page. |
| `dataset_deletion_browser.cjs:35` | Edit dataset locator targets a read-only Current page. |

These five scripts need a separately scoped update to the established lifecycle and
list contracts. Their scenario tails remain unverified by those scripts. Targeted
server regressions and the passing concept/version/browser suites provide separate
coverage; this is not a claim that the entire browser suite is green.

### Validation commands and results

Environment: Python **3.12.2**, SQLite **3.51.0**, PostgreSQL **16.15**
(`postgres:16`, Debian, aarch64), Node **24.9.0**. PostgreSQL ran in a disposable container
on loopback port 55484; the existing developer database/container was not used.
Artifact paths below are represented by `$ARTIFACTS`, an external temporary directory.
Test credentials are disposable fixture values only.

```sh
export REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55484/refrncr_test
.venv/bin/python -m pytest tests/test_record_system_status.py tests/test_dataset_diff.py tests/test_dataset_diff_views.py tests/test_dataset_versions.py tests/test_dataset_version_views.py tests/test_concept_lifecycle.py tests/test_record_import.py tests/test_import_wizard.py tests/test_user_management.py tests/test_interfaces.py tests/test_table_rows.py tests/test_authentication.py tests/test_login_throttling.py tests/test_login_javascript.py tests/test_side_panel_javascript.py tests/test_navigation_javascript.py tests/test_import_wizard_javascript.py tests/test_audit_time_javascript.py -q --junitxml="$ARTIFACTS/targeted.xml"
.venv/bin/python -m pytest --junitxml="$ARTIFACTS/pytest.xml"
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m pre_commit run --files tests/browser_ui_refresh_app.py tests/javascript/ui_refresh_browser.cjs
.venv/bin/python - <<'PYJS'
from pathlib import Path
import subprocess
files = sorted(Path('src/refrncr/static/js').glob('*.js')) + sorted(Path('tests/javascript').glob('*.cjs'))
for file in files:
    subprocess.run(['node', '--check', str(file)], check=True)
print(f'{len(files)} JavaScript files passed syntax checks')
PYJS
git diff --check
git diff origin/main --check
```

`node --check` was applied individually to every `src/refrncr/static/js/*.js` and
`tests/javascript/*.cjs` file: **23 passed**. Ruff lint passed; format-check reported
**155 files already formatted**. All seven configured all-files pre-commit hooks passed.
The additional invocation for the two untracked test files passed its five applicable
hooks; YAML/TOML hooks report no matching files (not skipped test scenarios).

Targeted pytest: **627 passed, 0 failed, 0 skipped, 1 warning**, 232.91 seconds.
The warning is the existing Starlette/httpx deprecation. The initial focused run had
2 new-test failures caused by an exact HTML-whitespace assumption (8 other cases passed);
the assertions now normalize header markup while preserving exact Field names. The
final targeted run includes both repaired engine cases. During browser-test development,
the fixture was corrected to use a valid two-column CSV, scrolling made independent of
OS keyboard shortcuts, close/scrollend event synchronization added, and the desktop scroll variant
kept distinct from the strict mobile no-overflow assertion. Production behavior was
not changed to accommodate those test setup issues.

Browser commands used the existing environment interface, one fresh fixture per script:

```sh
# Fixture, with PYTHONPATH=src and PYTHONDONTWRITEBYTECODE=1:
.venv/bin/python tests/browser_<fixture>_app.py
# Separate process, with REFRNCR_PLAYWRIGHT_MODULE pointing to the existing installation,
# REFRNCR_BROWSER_EXECUTABLE pointing to Google Chrome, and loopback URL/output configured:
node tests/javascript/ui_refresh_browser.cjs
node tests/javascript/dataset_diff_browser.cjs
node tests/javascript/concept_browser.cjs
node tests/javascript/dataset_versions_browser.cjs
node tests/javascript/audit_history_browser.cjs
node tests/javascript/navigation_browser.cjs
node tests/javascript/table_rows_browser.cjs
node tests/javascript/field_datatypes_browser.cjs
node tests/javascript/dataset_creation_browser.cjs
node tests/javascript/dataset_governance_browser.cjs
node tests/javascript/export_access_browser.cjs
node tests/javascript/audit_export_browser.cjs
node tests/javascript/dataset_deletion_browser.cjs
```

Fixture mapping: `ui_refresh:5186`, `dataset_diff:5184`, `concept:5185`,
`dataset_versions:5183`, `audit_history:5145`, `field_datatypes:5146`;
`navigation`, `table_rows`, `dataset_creation`, `dataset_governance`, `export_access`,
`audit_export`, `dataset_deletion` use `table_rows:5144`. Baseline runs use the same
fixture apps loaded through `runpy.run_path` and port +100, preventing cross-run state.
The temporary runner waits for successful HTTP readiness without sleeps, then invokes
the exact node command above and terminates the fixture. No browser assertions were
removed from the existing scripts.

Full configured pytest matrix with both SQLite and PostgreSQL enabled: **2,708 passed,
0 failed, 0 errors, 0 skipped, 3 warnings**, **513.36 seconds**. This includes migrations,
packaged CLI/startup, import, authorization, audit coverage, session security and all
REF-83/84 regressions. The three warnings are the existing Starlette/httpx deprecation
and two Python 3.12 SQLite datetime-adapter warnings from the historical REF-75 upgrade
test. No assertions or skips were added to make this matrix green.

Both Git whitespace checks passed. Final pre-commit all-files and new-file checks passed
as described above. The branch remains 20 commits ahead of origin/main, 0 behind; this
correction creates no additional commits. All temporary fixture servers and the dedicated
PostgreSQL container were stopped after validation. The twelve-file correction is
technically ready for a local commit within the five authorized findings. This does not
reclassify the five known baseline browser failures as successful or replace GitHub CI.

### Handoff

The correction has ten modified tracked files and two new untracked browser-test files,
listed above. The index is empty and HEAD remains the starting commit. No files were
staged, no commit created and nothing pushed. No unexpected files, debug application
code, real credentials, personal paths, temporary databases or browser screenshots were
introduced into the repository. Existing migrations, dependencies and CI configuration
are unchanged. This is a scoped local-commit handoff; independent GitHub CI remains the
authority before merge, and the five excluded browser failures remain documented debt.
Native screen-reader software was not exercised; semantic markup and actual Chrome
keyboard/focus interaction were tested.

Recommended commit message (not executed):

```text
fix(ui): resolve visual refresh review findings (REF-84)
```

Recommended explicit staging list, for the owner after reviewing this handoff (not executed):

```sh
git add docs/development/ref-84-report.md docs/development/ui-foundation.md \
  src/refrncr/static/css/components/dialog.css \
  src/refrncr/static/css/components/tables.css \
  src/refrncr/static/css/layouts/auth.css \
  src/refrncr/templates/pages/auth/login.html \
  src/refrncr/templates/pages/datasets/_record_table.html \
  tests/test_record_system_status.py tests/browser_ui_refresh_app.py \
  tests/javascript/ui_refresh_browser.cjs tests/javascript/concept_browser.cjs \
  tests/javascript/dataset_diff_browser.cjs
```

Recommended push command, only after the owner creates the local commit (not executed):

```sh
git push -u origin feat/ui-visual-refresh
```
