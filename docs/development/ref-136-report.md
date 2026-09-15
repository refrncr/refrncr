# REF-136 — Basic Record search

## Final scope

The product owner explicitly reduced scope to basic search after reviewing the first
implementation: “remove the add filter functionality, this is too much for now. The
basic search works very fine”. This report describes that final implementation.
Typed Field filters are deferred: the picker, editor, operators, filter compilation,
service argument, URL grammar and progressive filter controller have been removed.
Existing filter URLs fail closed with a safe 400 and a clear/reset link.

Community/core implementation on `feat/ref-136-record-search-filters`, based on
`origin/main` at `9460e63`. The initial tree was clean. Nothing has been staged,
committed, pushed or merged. Jira has not been edited; this approved scope reduction
should be reflected in the story before claiming all original acceptance criteria.

Context read before implementation: REF-136, REF-123, REF-130, REF-155, REF-160 and
REF-162; repository Record, datatype, version, Concept, security/audit, UI and import
contracts; relevant Confluence architecture/domain, security and product pages.
[REF-136](https://cleverhorizons.atlassian.net/browse/REF-136) is the product story.
No architecture change requires an ADR. No Cloud functionality is introduced.

## Search semantics

- The visible Search button and Enter explicitly apply the general term.
- Search covers String Fields only, including String identifiers, as confirmed by
  the owner. Non-String identifiers and numeric/temporal/boolean presentation text
  are excluded. There is no conversion through display formatting.
- The existing String canonicalizer trims and normalizes NFC; Unicode casefold
  supplies case-insensitive literal substring matching. `ams` finds `Amsterdam`,
  `STRASSE` finds `Straße`, and `%` / `_` are literal characters.
- Search input is limited to 200 characters. Empty/whitespace search means no term.
  Missing/null String values do not match a nonempty term.
- Only Field identity and datatype from the displayed version participate. Removed,
  recreated or incompatibly retyped retained values are never reinterpreted.
- Search and status combine with AND across the complete authorized version set,
  before the existing 50-row pagination. Result counts are exact. Header metadata
  counts remain version-wide. The established canonical identifier/UUID ordering
  and Record actions are unchanged.

## Status and URL contract

Existing Current, Concept and immutable-version Records routes remain. Parameters:

| Parameter | Meaning |
| --- | --- |
| `q` | Applied String search term |
| `record_status` | `active` default; `deleted` and `all` only for Concept |
| `page` | Canonical positive ASCII integer, at most nine digits |
| `import_results=1` | Existing import receipt projection; defaults status to All |
| repeated `status` | Distinct Added/Changed/Deleted import selections |
| `status_selection=1` | Existing explicit import checkbox selection |
| `action` | Transient `search` or `apply`; 303 redirects omit action and page |

Unknown/duplicate parameters, obsolete Field-filter parameters/actions, malformed
pages/statuses and oversized inputs are rejected without partial results or echoed
values. The query limit is 32 KiB and individual inputs use the existing 4,096
codepoint bound before the stricter search-term check.

Every submitted search/status change resets to page 1. Pagination preserves applied
criteria and pins published links to the selected immutable version. Refresh,
Back/Forward and shared URLs restore criteria. A live Concept may change between
requests; there is no persisted browser snapshot. An out-of-range page shows 0–0
with the exact total and a First page recovery link preserving search.

Status uses a separate form carrying the last applied term. Editing an unsubmitted
search draft does not apply it through a status change; that draft is not persisted
across navigation. Clear all resets search, status/import selection and page.
Clearing only the import-change menu retains the general search and status context.
The internal read service keeps its previous All default; HTTP defaults to Active.

## UI and accessibility

`components/record_search.html` composes existing search/control tokens and native GET
forms. The search controls sit inside the table toolbar, sharing its border above the
column headings, as requested in the supplied design. The desktop toolbar contains
the search field, Search and conditional Clear all; mobile puts the input above the actions. The placeholder provides the search hint without a duplicate explanatory line.
There is no Add filter or Apply filters control. Concept retains Apply status.

`record-search.js` adds immediate status submission and a polite result-count live
announcement. Automatic status navigation restores focus to the status selector.
Search/Enter, status Apply, clearing, pagination and Record links all
work without JavaScript. Existing keyboard Record-panel open/Escape/focus return is
preserved. Controls retain labels and existing visible focus. No new tokens/global
controls/dependencies/frameworks are introduced; the Records toolbar remains the
specific component variant. No unrelated page rules were added.

Chrome 152.0.7977.84 checked 320, 768 and 1,440 px plus no-JavaScript at 320 px.
Eight layout measurements in `/private/tmp/ref136-basic-browser/measurements.json`
show no horizontal page overflow. Screenshots are outside the repository in that
same directory. The runner verifies Search/Enter, status changes without applying a
draft term, pagination/refresh/Back/Forward, shared URLs, clear, absent Add filter,
Current/Concept separation, Record-panel keyboard operation and out-of-range recovery.
The in-app browser was unavailable during bootstrap; actual installed Chrome uses
the repository's existing external Playwright convention. No human screen-reader
certification is claimed.

## Security, data and query strategy

Existing server-side Dataset, Concept and Scheduled authorization stays authoritative;
service selection precedes search validation. All reads stay within the existing
coherent version snapshot. No mutation, revision, import/export, schema, migration,
tenancy, dependency or audit contract changes. Search emits no audit event.

Safe fixed errors exclude criteria and inaccessible values. Records responses use
`no-store` and `no-referrer`. The supported CLI Uvicorn access handler redacts Records
query strings (including trailing-slash attempts) using a copied logging config.
Unexpected access-event shapes fail closed. External proxies/hosting stacks need
equivalent log policy. Browser history and deliberately shared URLs contain criteria.

The existing joined/batched reads load the complete version set and canonical Python
sorting runs before matching/pagination. No SQL string is built from criteria and
there is no N+1 search query. Existing Dataset/version/Record-value indexes support
loading, with no new index or migration. Search is not pushed into database-specific
collations: this preserves matching parity across SQLite and PostgreSQL.

Scalability limit: full-set memory O(populated values) and sort O(R log R). The retained
1-versus-30-record and 1,000-record tests verify bounded SELECT counts and capture
engine EXPLAIN plans/timing; they are local fixtures, not production guarantees.
Final local 1,000-record measurements:

| Engine | Concept | Current | SELECTs Concept / Current |
| --- | --- | --- | --- |
| SQLite 3.51.0 | 0.1379 s | 0.0653 s | 15 / 12 |
| PostgreSQL 16.15 | 0.1790 s | 0.1171 s | 18 / 12 |

Separate 1-versus-30-record reads stayed at 15 SELECTs on SQLite and 18 on PostgreSQL.
The measurement run passed 4 tests (88 deselected) in 7.05 seconds. Full EXPLAIN plans
and properties are in `/private/tmp/ref136-basic-performance.xml`. Existing indexes
support scoped loading; these are fixture plans, not production EXPLAIN ANALYZE.
The removed typed-filter implementation's previous timings are not used here.

## Validation

- Ruff lint, formatting, pre-commit and `git diff --check`: passed before pytest.
- Focused SQLite regression: **257 passed, 74 skipped, 1 warning**, 47.05 seconds.
- Final search-specific regression on both engines: **92 passed**, 72.87 seconds;
  includes two additional obsolete-URL cases added after full-suite collection.
- Final complete SQLite suite: **2,497 passed, 1,013 skipped, 3 warnings**, 620.27 seconds.
- Final complete PostgreSQL 16 suite: **3,510 passed, 3 warnings**, 1,114.53 seconds.
- Unchanged import Chrome acceptance: passed at the existing viewport/no-JavaScript matrix.
- Final basic-search Chrome acceptance: passed, eight responsive measurements,
  no JavaScript page errors; includes native no-JavaScript flow.

The full-suite warnings are the existing Starlette/httpx deprecation and SQLite
datetime-adapter deprecations; no failing assertion or suppressed check remains.

The complete suite covers existing import/export, authorization, typed sorting,
Record status/actions and JavaScript controllers alongside revised search tests.
Tests dedicated to removed typed filters were removed; existing basic-search,
non-String exclusion, scope, pagination, privacy and N+1 coverage remains. Obsolete
filter URLs are explicitly covered by rejection tests.

An initial sandboxed SQLite-oriented run failed its mandatory PostgreSQL connection
smoke test with Operation not permitted. It was interrupted and restarted with local
connection access, without changing or weakening the test.

Full-run logs: `/private/tmp/ref136-basic-sqlite-final.log` and
`/private/tmp/ref136-basic-postgres.log`; focused log:
`/private/tmp/ref136-basic-focused.log`; Chrome log:
`/private/tmp/ref136-basic-chrome.log`.

Environment: Python 3.12.2, SQLite 3.51.0, PostgreSQL 16.15, SQLAlchemy 2.0.52,
psycopg 3.3.4, pytest 8.4.2, Ruff 0.16.5, pre-commit 4.6.2, Chrome 152.0.7977.84.
PostgreSQL uses a disposable `postgres:16` container bound to loopback port 55436;
the developer's existing database is not mutated. The SQLite-oriented suite also
runs its existing read-only SELECT 1 smoke test against the default local database. Independent GitHub CI remains
required before merge and has not been run because nothing is pushed.

## Changed files and explicit staging list

The following commands are recommendations only, not executed. The commit title is
updated for the owner's final basic-search scope.

```sh
git add \
  docs/development/audit-policy.md \
  docs/development/browser-session-security.md \
  docs/development/dataset-context.md \
  docs/development/dataset-versions.md \
  docs/development/field-datatypes.md \
  docs/development/record-import-security.md \
  docs/development/ref-136-report.md \
  docs/development/ui-foundation.md \
  src/refrncr/cli.py \
  src/refrncr/dataset_versions.py \
  src/refrncr/record_search.py \
  src/refrncr/static/css/components/tables.css \
  src/refrncr/static/js/record-search.js \
  src/refrncr/templates/components/record_search.html \
  src/refrncr/templates/components/record_status_filter.html \
  src/refrncr/templates/pages/datasets/_record_table.html \
  src/refrncr/templates/pages/datasets/records.html \
  src/refrncr/templates/pages/datasets/search_error.html \
  src/refrncr/web.py \
  tests/browser_record_search_app.py \
  tests/javascript/record_search_browser.cjs \
  tests/test_cli.py \
  tests/test_dataset_browser.py \
  tests/test_record_search.py \
  tests/test_record_system_status.py
git commit -m "feat(records): add basic record search (REF-136)"
git push -u origin feat/ref-136-record-search-filters
```

`git diff origin/main...HEAD` and its stat are empty because this branch has no
commits yet. Review the working diff and all new files listed above. Nothing is
staged, committed or pushed.

## Table-toolbar visual refinement

At the owner's request, the search controls were moved inside the Record table's
existing bordered toolbar, immediately above the column headings. Result count and
published-version note remain in that toolbar. The Records toolbar variant scopes
form sizing and wraps on mobile; the generic toolbar count rule now targets direct
children so nested import badges do not inherit count layout. No tokens, global
controls, search behavior or server contracts changed.

Focused validation after this template/CSS change: **185 passed, 44 skipped**,
35.16 seconds (PostgreSQL not injected). Ruff, formatting, pre-commit and diff checks
passed. Chrome verification uses the existing 320/768/1440 px and no-JavaScript
320 px matrix; evidence is in `/private/tmp/ref136-table-toolbar-browser/`. Earlier
full-suite results describe the server implementation before this visual refinement.
