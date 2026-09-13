# REF-155 — discard an active Concept

Review and validation: 2026-09-13. The ADR-015 clarification is accepted;
implementation is ready for local review and commit after the checks recorded below. Jira:
[REF-155](https://cleverhorizons.atlassian.net/browse/REF-155).
Branch: `feat/ref-155-discard-concept`, based on `origin/main` at `5d5c808`.
Community/core functionality; no Cloud infrastructure or tenancy is introduced.
Nothing has been staged, committed, pushed or merged. GitHub CI has not run for
this uncommitted change; it remains the independent gate before merge.

## Product decision and lifecycle

An authorized owning-team member or active Community administrator can discard
**every active Concept for which Current exists, including an unchanged Concept**.
The complete active Concept is deleted; this is neither a reset nor automatic
replacement. Success redirects with HTTP 303 to the Dataset overview in Current
context, states that Current was not changed and offers explicit **Create concept**.

Without Current, including Scheduled-only publication, the menu action is visible,
focusable and disabled. Its associated visible explanation is exactly:

> This Concept contains the Dataset’s first unpublished version. There is no Current version to return to. Publish it first or delete the Dataset.

Both GET review and service confirmation enforce this boundary. Dataset name and
description remain intact when discard is unavailable. No technical fallback name
or migration for original metadata is used. The clarification is in Jira REF-155.

**Approved ADR-015 clarification:** REF-155 does not change Concept creation.
The user story now says “start a fresh Concept from the applicable published or
scheduled base version.” Jira includes the acceptance rule:

> After discard, creating a new Concept continues to use the existing ADR-015 base selection: Scheduled when applicable, otherwise Current.

Discard preserves both snapshots and returns to the Dataset overview. A subsequent
explicit Create concept uses the existing base selection automatically. Regression
assertions on both engines cover Scheduled selection and fallback to Current after
Scheduled cancellation, fresh Concept identity, no initial changes and unchanged
snapshot tables after both discard and recreation. Application logic is unchanged
by this clarification.

## Architecture, ownership and deletion

The existing `DatasetVersionService`, SQLAlchemy `ConceptOperations` adapter and
Unit of Work own the operation. `dataset_concepts` is the explicit active lifecycle
row with its own UUID, pinned baseline, creator, timestamps and start revision.
Working tables contain Concept content; version tables hold independent immutable
snapshots. Removed Record markers are baseline projections, not another mutable
state table. No new data model, dependency, framework or schema is introduced.

The Dataset parent contains stable identity/governance and working name/description.
After discard these two properties and content freshness come from Current;
Dataset revision advances once rather than returning to a historical counter.

The serialized transaction performs:

1. Lock the Dataset and recheck owning-team/admin authority through existing
   Dataset → team → actor lock ordering.
2. Require the active Concept and Current; recompute summary counts and compare
   the exact confirmation identity. Recheck calendar-selected Current after counting.
3. Delete source-format proposals for this Dataset's working Field IDs.
4. Delete working Record values, then Records including revisions.
5. Delete identifier membership, then working Fields including order/definitions.
6. Delete the active Concept row; restore Current name/description and publication
   timestamp as content freshness; increment Dataset revision.
7. Write exactly one bounded discard audit and commit everything together.

The six DELETE statements have explicit Dataset/Field predicates. Working values
have composite Record/Field FKs; identifier membership references Fields;
source-format proposals reference Field IDs. Child-first deletion does not depend
on incidental cascades. A second Dataset's active work is independently tested.

Preserved: Dataset UUID/key/creator, owning team, grants, memberships, export access,
users/teams, all pre-existing audit history and every Current, Scheduled, Expired
and Cancelled snapshot, including its Fields, identifiers, Records and values.
Snapshot source UUIDs do not FK-reference mutable working rows. There is no
replacement Concept, partial discard, undo, Scheduled cancellation or historical
restore. Subsequent explicit creation uses the existing snapshot initialization
with monotonic Record revisions.

## Confirmation, concurrency and invalidation

The signed, session-bound confirmation captures Dataset UUID, Concept UUID,
Dataset revision, Concept start revision and updated-at, actor UUID, owning-team
UUID and Current snapshot UUID. HMAC domain separation prevents substituting a
normal review token. HTTP POST accepts exactly one each of CSRF, confirmation and
explicit confirmation fields. Authorization precedes identity/conflict disclosure.

PostgreSQL row locks and SQLite `BEGIN IMMEDIATE` share the existing writer boundary.
Stale metadata, Field add/edit/delete/order, identifier changes, Record add/edit/delete,
import, publication, owner changes and calendar activation cannot authorize old
confirmations. Membership removal denies access. Concurrent Record/import/publish
and discard yield one coherent winner. Duplicate discards have at most one effect.
Old confirmation identities cannot delete a newly created Concept.

Failure injection after value, Record, Field and Concept deletion, after audit flush
and at commit verifies exact rollback. A real HTTP failure confirms fixed diagnostic
logging with no exception details, source rows, filename or Record values.

Staged imports bind their original Concept UUID in addition to actor/session/Dataset.
Executable reads and Confirm's locked write transaction reject absent or replacement
Concepts. Successful HTTP discard purges the local store, including busy claims.
Direct service callers/other processes get immediate logical invalidation; physical
memory cleanup follows obsolete-state lookup/resume, session revocation or the
existing lazy 15-minute TTL. There are no staged upload files. A late `finish()`
cannot restore a purged claim. Remembered source-format proposals are deleted with
working Fields; they are operational state, not published snapshot content.

Concept exports pin the Concept UUID in a coherent read snapshot and recheck
existence/identity and maintenance authority in the existing pre-return audit write
transaction. Discard or Dataset deletion winning that boundary closes the spool,
returns no artifact and writes no export-success event. A previously accepted,
audited stream cannot be recalled. Current/historical exports retain ADR-012
snapshot semantics, including concurrent Dataset deletion. There are no persisted
export jobs or download tokens. This intentional Concept lifecycle exception is
recorded in ADR-012, not hidden as a generic export refactor.

Concept routes are `no-store`. With no active Concept, old edit/configuration/import/
export/Concept URLs and POSTs fail safely without automatic creation. Generic Concept
URLs identify the currently active Concept; after explicit creation they naturally
open that new Concept. The signed discard/review and staged-import identities remain
bound to their original lifecycle and cannot be reused. Browser Back/Refresh do not
replay the destructive operation or recreate the discarded lifecycle.

## Authorization, audit and impact review

The existing maintenance policy remains authoritative: owning-team members and
active Community admins only; Viewer/Editor grants or export grants alone are
insufficient. Unauthorized direct service calls and HTTP endpoints use the existing
non-disclosing not-found response. There is no new tenant, role or permission model.
CSRF, escaping, request binding, transaction boundaries and independent export
permissions remain enforced server-side.

One `dataset.concept_discarded` event records the trusted actor and Dataset UUID,
Concept UUID and seven bounded counters: metadata changes plus Fields/Records
Added, Changed and Removed. No Record values, descriptions, source data or filenames
enter this event. Denied, stale, duplicate, unavailable and rolled-back operations
write no successful discard event. Opening the dialog is a read and does not create
a diff-viewed event. Audit history labels explicitly present the new context and
identify Concept deletion without marking the Dataset as deleted. Catalog, inventory,
coverage and guard contracts are updated; security-attempt logging remains REF-31.

Impact: authorization reuse and rechecks; Dataset-scoped persistence isolation;
no schema/Alembic migration; one required web endpoint and internal discard contracts;
no external/public API redesign; no dependency change; documentation/ADR-012 updated.
Existing installation upgrades use the existing migration head. Restarting processes
also removes process-local staged state; no backfill is required.

## UI, accessibility and responsive evidence

Product functionality uses the shared Concept component, native dialog, feedback,
buttons and sidepanel controller. The action is in a destructive menu position.
The dialog identifies Dataset/Concept, explains permanent loss, unchanged snapshots
and next steps, and presents counts without Record values. Initial focus is on the
warning. Native modal containment, Tab/Shift+Tab, Escape, Cancel, menu reopening and
focus restoration are exercised in real Chrome. Confirmation is keyboard-operable
with visible outline. Stale fragments replace the form with current counts/token and
focus the alert. A controlled 503 exercises the shared safe error and retry path.
The complete full-page flow works without JavaScript.

Shared-controller tests cover repeated initialization, one listener/request per
pending submission, fragment replacement, late responses, focus restoration and
network/server errors. No fixed sleeps or timing assertions were added.

Changes to the design system:

- Tokens: none.
- Shared components: Concept menu/dialog macros; sidepanel focus restoration reopens
  the containing details menu; reusable creation macro accepts a display label.
- Variants: bounded Concept menu with wrapping disabled help; `detail-list-counts`
  reserves space for count labels and auto-sizes the number column.
- Deliberate page behavior: Current success message and Create concept action;
  discard review content and exact first-unpublished explanation.

The first visual review found a cramped label column at 768 px. The counts variant
fixes it and subsequent Chrome assertions check actual label scroll width. All seven
counts remain reachable in the scrollable panel, while footer buttons stay visible.
No duplicated dialog IDs/menu markup in embedded Record panels remain.

Chrome **152.0.7977.84**, viewports **320×950, 768×950, 1440×950**. Final discard run:
**42 measurements/screenshots**, zero page errors, zero unexpected console errors.
Chrome emits resource errors for the deliberately tested 409/503 responses; all
18 expected HTTP failures and their console entries are retained in the evidence.
This is not a claim of zero console entries or a manual screen-reader audit.

| Viewport | Page scroll width | Dialog left–right | Dialog top–bottom | Footer bounds |
|---|---|---|---|---|
| 320 | 320 | 0–320 | 0–950 | x16–304, y898–934 |
| 768 | 768 | 445.453–768 | 0–950 | x470.453–744, y890–926 |
| 1440 | 1440 | 928–1440 | 0–950 | x953–1416, y890–926 |

Evidence is outside the repository:
`/private/tmp/ref155-browser-discard-final/results.json` and adjacent PNGs.
Representative images: `mixed-320.png`, `mixed-768.png`, `mixed-1440.png`,
`disabled-320.png`, `stale-320.png`, `server-error-320.png`, `nojs-320.png`.
The existing Concept, version and diff Chrome scripts also pass on all three widths;
the version script additionally checks imports, Current/historical downloads and
no-JavaScript publication. Its mapping button selector was updated to the already
established “Continue to preview” label, with its behavior assertions retained.

## Database and performance evidence

Python **3.12.2**, SQLite **3.51.0**, PostgreSQL **16.15**
(`Debian 16.15-1.pgdg13+2`, disposable local `postgres:16`), Node **24.9.0**.
Both database fixtures use real constraints and isolated data. PostgreSQL tests use
unique schemas, not a SQLite substitute. SQLite foreign keys are enabled by the
existing engine factory. Concurrent tests coordinate with Events/Barriers, not sleeps.

Confirmation uses existing SQL diff aggregates and Field classification. It does not
materialize Record/value graphs. Field definitions are materialized as before.
Deletion is set-based and writes one audit, not an event/delete loop per Record.
Measured empty and 1,000-Record mixed Concepts:

| Engine | Records | Review statements | Confirm statements | DELETEs | Review+confirm seconds |
|---|---:|---:|---:|---:|---:|
| SQLite | 0 | 19 | 30 | 6 | 0.0208 |
| SQLite | 1,000 | 19 | 30 | 6 | 0.0406 |
| PostgreSQL 16 | 0 | 20 | 31 | 6 | 0.0603 |
| PostgreSQL 16 | 1,000 | 20 | 31 | 6 | 1.1054 |

Captured in `/private/tmp/ref155-focused.xml` via test properties in the 106-case
focused run, before the additional export-removal scenarios. These are observations on a shared development machine,
not performance budgets or production throughput promises. Query count does not grow
with Record count in these cases; aggregate cost still scales with data volume.
No query cache, index, schema or speculative performance framework is introduced.

## Validation commands and results

All Python commands use `.venv/bin/python`. Before pytest, run Ruff lint, Ruff format
check, pre-commit and whitespace checks. Untracked files are explicitly included in
an additional pre-commit invocation because `--all-files` follows tracked files.

The full SQLite-configuration run passed: **2,329 passed, 876 skipped, 3 warnings**,
512.56 s. The 876 skips are existing PostgreSQL-dependent cases with the PostgreSQL
URL unset. No new skip/xfail is introduced. The full PostgreSQL-enabled run passed: **3,205 passed, zero skips, 3 warnings**,
830.88 s. It includes the SQLite cases and all 876 PostgreSQL-dependent cases.
PostgreSQL log/XML: `/private/tmp/ref155-full-postgres-final.log` and `.xml`.
SQLite log/XML: `/private/tmp/ref155-full-sqlite-final.log` and `.xml`.

Completed evidence:

- After the ADR-015 clarification, the complete discard and Concept lifecycle modules
  passed again on SQLite and PostgreSQL 16: **144 passed**, zero skips, **46.10 s**.
  This includes the strengthened Scheduled/Current selection and snapshot-preservation
  assertions. Log: `/private/tmp/ref155-adr015-validation.log`.
  Command with the disposable PostgreSQL URL set:
  `.venv/bin/python -m pytest tests/test_concept_discard.py tests/test_concept_lifecycle.py -q --tb=short`.
  Only Jira text, tests and documentation changed after the full-suite/browser runs;
  application logic and UI remain identical to those fully verified runs.

- Final targeted discard/audit-history/Dataset-deletion suite: **278 passed**, no skips,
  127.86 s, SQLite and PostgreSQL 16. This includes all **108 REF-155 cases**
  (54 per engine). Log: `/private/tmp/ref155-final-targeted.log`.
- Chrome discard: three viewports, 42 measurements, no unexpected console/page errors.
- Existing Concept, version and diff Chrome scripts: passed.
- JavaScript syntax and shared sidepanel controller: passed. The full SQLite run
  includes **7 JavaScript wrapper cases**, **43 CLI cases**, **1 package case** and
  **5 installed sdist/wheel cases**, including console help and live HTTP startup.
  These are subsets of the full total, not additional tests.

Initial broad runs exposed missing audit context labels and an obsolete audit-event
count (34 → 35). They were corrected. The old pending-Concept-export deletion test
was split into the unchanged published-snapshot guarantee and deterministic Concept
invalidation; assertions were not removed to mask a failure. Exploratory full runs
were interrupted for these corrections and are not counted as successful validation.
The final runs above contain every collected test. The three warnings are existing
Starlette/httpx and SQLite datetime-adapter deprecations; no new skips or xfails
were introduced. Ruff lint/format, pre-commit (tracked and explicit untracked files)
and `git diff --check` passed. Final quality log: `/private/tmp/ref155-quality-final.log`.

```shell
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
git diff --check
# With the disposable PostgreSQL URL set:
.venv/bin/python -m pytest tests/test_concept_discard.py tests/test_audit_history.py tests/test_dataset_deletion.py -q --tb=short
# PostgreSQL URL is the disposable CI-style test DB; each fixture isolates its schema.
REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@localhost:5432/refrncr_test .venv/bin/python -m pytest -v --tb=short -o junit_family=xunit1 --junitxml=/private/tmp/ref155-full-postgres-final.xml
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -v --tb=short -o junit_family=xunit1 --junitxml=/private/tmp/ref155-full-sqlite-final.xml
node --check src/refrncr/static/js/refrncr.js
node --check tests/javascript/concept_discard_browser.cjs
node --check tests/javascript/dataset_versions_browser.cjs
node tests/javascript/side_panels.cjs src/refrncr/static/js/refrncr.js
```

Browser commands use the existing external test-only Playwright install:
`REFRNCR_PLAYWRIGHT_MODULE=/private/tmp/ref159-browser/node_modules/playwright`,
`REFRNCR_BROWSER_EXECUTABLE=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.
Start each disposable fixture with `PYTHONPATH=tests .venv/bin/python tests/<fixture>`.
Run the corresponding `node tests/javascript/<script>` with
`REFRNCR_BROWSER_OUTPUT=/private/tmp/<evidence-directory>`. No browser dependencies
were added to the application. The Browser skill was read and its bootstrap attempted;
the in-app backend returned “Browser is not available: iab”, so standalone actual Chrome
was used as fallback.

| Fixture | Script | Evidence directory |
|---|---|---|
| browser_discard_app.py | concept_discard_browser.cjs | ref155-browser-discard-final |
| browser_concept_app.py | concept_browser.cjs | ref155-browser-existing |
| browser_dataset_versions_app.py | dataset_versions_browser.cjs | ref155-browser-versions |
| browser_dataset_diff_app.py | dataset_diff_browser.cjs | ref155-browser-diff |

## Remaining limitations and review state

The product owner resolved the creation-policy question in favor of unchanged ADR-015.
No product-scope blocker remains; the branch is prepared for local review and commit.
Both complete database configurations and the targeted/browser/quality checks passed. GitHub CI cannot be claimed without a committed/pushed
change. Physical staged-memory cleanup outside the HTTP process is lazy, and accepted
export streams cannot be recalled; the fail-closed boundaries are documented/tested.
Manual assistive-technology testing and production-scale performance were not done.
No migration or dependencies were added. No staging/commit/push was performed.

## Explicit staging list and suggested commands

The complete intended file list is generated below from the reviewed local working
tree. Do not run the staging, commit or push commands until local review is accepted.
`git diff origin/main...HEAD` is intentionally empty because this work is uncommitted;
`git diff origin/main` plus the untracked files is the actual review surface.

Recommended commit: `feat(concepts): discard active concepts safely (REF-155)`.
Recommended push: `git push -u origin feat/ref-155-discard-concept`.

```text
docs/architecture/adr-012-audit-export-boundary.md
docs/development/audit-coverage.json
docs/development/audit-inventory.json
docs/development/audit-policy.md
docs/development/dataset-export-security.md
docs/development/dataset-governance.md
docs/development/dataset-versions.md
docs/development/record-import-security.md
docs/development/ref-155-part-1-notes.md
docs/development/ref-155-report.md
docs/development/ui-foundation.md
src/refrncr/audit.py
src/refrncr/audit_presentation.py
src/refrncr/concept_contracts.py
src/refrncr/concept_persistence.py
src/refrncr/dataset_versions.py
src/refrncr/ports.py
src/refrncr/record_import.py
src/refrncr/review_tokens.py
src/refrncr/services.py
src/refrncr/static/css/layouts/configuration.css
src/refrncr/static/css/layouts/page.css
src/refrncr/static/js/refrncr.js
src/refrncr/templates/components/concept.html
src/refrncr/templates/pages/datasets/concepts/discard.html
src/refrncr/templates/pages/datasets/detail.html
src/refrncr/templates/pages/datasets/versions/list.html
src/refrncr/web.py
tests/browser_discard_app.py
tests/javascript/concept_discard_browser.cjs
tests/javascript/dataset_versions_browser.cjs
tests/javascript/side_panels.cjs
tests/test_audit_catalog.py
tests/test_audit_history.py
tests/test_concept_discard.py
tests/test_dataset_deletion.py
```
