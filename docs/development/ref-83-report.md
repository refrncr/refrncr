# REF-83 implementation and validation report

Status: both parts implemented and locally validated; technically ready for an authorized commit.
No commit or push performed. GitHub CI is pending and remains the independent pre-merge gate.

## Product decision

The product owner explicitly withdrew the separate editor/publisher wording on
2026-09-10. Active owning-team members and Community administrators may edit
concepts, publish and cancel Scheduled versions. Viewer/editor/legacy admin Grants
remain inert; additional export access grants no mutation authority.
All decisions use the existing `DatasetAuthorizationService`. No new role exists.

## Inventory and plan

See [ADR-014](../architecture/adr-014-dataset-versions.md) for the inspected working
tables, stable identifiers, snapshot model and transaction/date decisions.
Extend models, ports, persistence and composition; add one version service and one
new migration with preflight. Update mutation revision tracking and import stale
confirmation, central audit catalog/inventory/coverage and deletion cleanup.
Verify real SQLite/PostgreSQL constraints, immutable content, canonical comparison,
authorization, date boundaries, rollback, concurrency and migration retry.
Part 2 follows only after the part-1 report and validation.

No API, background worker, separate editor role, historical permission model,
delta reconstruction or detailed diff is included. REF-84/86/87/88/107/108/155/156
remain outside scope.

## Part 1 implementation

The existing working tables remain the concept. Migration `0015_ref_83` adds the
positive Dataset revision and four complete-snapshot tables: `dataset_versions`,
`dataset_version_fields`, `dataset_version_records`, `dataset_version_values`.
Stable source Field/Record UUIDs are scoped by version. Composite foreign keys and
unique constraints protect value membership, identifiers, Field keys/order and
Dataset/version numbers. Canonical keys plus text retain every supported datatype.
Names, description, ordered definitions, identifier membership and all Record values
are copied; ownership, memberships, Grants and export access remain current only.

`DatasetVersionService` validates and compares under the same Dataset lock used by
all working writers. PostgreSQL uses row locks and SQLite uses `BEGIN IMMEDIATE`.
Expected revision rejects stale publication and duplicate submits; import fingerprints
now include that revision. Content changes, publication and cancellation advance it;
true no-ops and administrative changes do not create a content diff. Complete copy,
revision and one catalogued audit event share a single commit/rollback boundary.
Neither narrow persistence ports nor immutable results expose snapshot editing or
individual deletion. ORM snapshot update/delete attempts are rejected. Privileged raw
SQL is outside this application-level immutability claim.

UTC calendar dates use inclusive starts and exclusive successor boundaries. Only
cancellation actor/time is persisted as status information; Scheduled/Current/Expired
and displayed end dates are derived without jobs. A same-day successor leaves its
predecessor with an empty interval. Cancellation retains the number and entire copy.
REF-141 removes all operational version rows with the Dataset and preserves audit.

Stopped-writer preflight validates all existing Datasets before DDL or snapshot writes,
reports all invalid IDs and permits correction/retry. Each valid Dataset gets one
System-published Version 1 on the UTC upgrade date. Ownership/access/audit are retained.
The complete migration transaction rolls back on copy failure on both databases.
Populated downgrade requires restoring the matching pre-upgrade backup.

Audit uses exactly `dataset.version_published` and `dataset.version_cancelled`, with
trusted actor, stable Dataset ID, bounded name, version number, effective date and
Field/Record counts. No values, descriptions or release notes enter audit. The existing
catalog, inventory, matrix and automatic discovery/bypass guards include the new
service and operator operations. Read presentation retains safe names after deletion.

## Part 1 validation ledger

All commands use the repository Python 3.12 virtual environment. PostgreSQL-enabled
commands use the disposable test URL injected through `REFRNCR_TEST_POSTGRES_URL`.
Actual engines: SQLite 3.51.0 and PostgreSQL 16.15. No developer PostgreSQL 15 instance
was used for the reported PostgreSQL tests.

- `.venv/bin/python -m pytest -q tests/test_dataset_versions.py tests/test_dataset_version_migration.py tests/test_audit_catalog.py --tb=short`:
  201 passed, zero failures/skips/warnings (before the additional CLI/timestamp tests).
- `.venv/bin/python -m pytest -q tests/test_dataset_version_migration.py tests/test_dataset_versions.py --tb=short`:
  154 passed, zero failures/skips/warnings after adding populated downgrade protection,
  CLI preflight/upgrade and UTC timestamp round-trip checks.
- `.venv/bin/python -m pytest -q tests/test_dataset_versions.py -k 'serializes or serialize' --tb=short`:
  24 passed, 78 deselected, zero failures/skips/warnings. These stronger tests observe
  the competing SQL lock attempt before releasing the first writer; no sleeps.
- The first full `.venv/bin/python -m pytest -q --tb=short` run found 70 failures,
  with 2,353 passed and three warnings. Failures exposed historical fixtures tied to
  the previous head/schema and audit presentation fixtures missing the two contracts.
  Historical migration tests now pin the migration they actually exercise and use
  historical SQL projections instead of the current ORM on old Dataset tables.
  Previously valid full-chain fixtures now provide a publishable schema; deliberately
  unpublishable historical fixtures additionally assert REF-83 refuses their upgrade.
  No historical migration or production preflight was relaxed.
- `.venv/bin/python -m pre_commit run --all-files`: all configured hooks passed after
  correcting a long test SQL literal. Explicit Ruff also checks newly created files.
- Final full `.venv/bin/python -m pytest -q --tb=short` with PostgreSQL enabled:
  **2,431 passed, 0 failed, 0 skipped, 3 warnings**, 522.73 seconds. Both databases
  run in this suite. The additional tests and final migration correction below were
  verified separately after this run's collection.
- `.venv/bin/python -m pytest -q tests/test_dataset_versions.py -k bounded_read --tb=short`:
  **2 passed, 102 deselected**, no failures/skips/warnings. One and 101 Records take
  the same number of review SELECTs (at most 12 including authorization); full database
  state remains unchanged. The initial guessed bound of ten excluded authorization reads
  and was corrected to the measured twelve.
- Final review found the canonical-key length check present in the ORM but absent
  from the new migration. The migration now includes it; an explicit constraint-parity
  test covers all four snapshot tables on both engines.
- `.venv/bin/python -m pytest -q tests/test_dataset_version_migration.py tests/test_field_datatype_migration.py tests/test_dataset_governance_migration.py tests/test_migrations.py --tb=short`:
  **133 passed**, no failures/skips/warnings, 31.71 seconds after that correction.
- `.venv/bin/python -m ruff check .`: passed.
- `.venv/bin/python -m ruff format --check .`: 134 files already formatted.
- `git diff --check`: passed.
- `.venv/bin/python -m pre_commit run --all-files`: all configured hooks passed.
  A separate `pre_commit run --files` over every new untracked file also passed;
  YAML/TOML hooks correctly had no applicable new files. No files were staged.

The authorization clarification and future effective-dating contract are also recorded
in Confluence's API Architecture & Effective Dating page (version 2). No API availability
or deployment is claimed.

Three existing full-suite warnings concern the Starlette/httpx transition and the
Python 3.12 SQLite datetime adapter (two occurrences). They are unrelated to REF-83.

## Part 2 implementation

Existing Dataset/Field/identifier/Record read services now select content through the same
`DatasetVersionService` and current `DatasetAuthorizationService`. Ordinary users see Current
snapshots only, including snapshot names/counts in lists. No Current means no ordinary list entry
or detail. Dataset administrators retain concept management. Explicit historical configuration,
Records and pagination use only the selected version; deleting/reusing live source objects does
not alter their content. Current/Expired history is public to authorized active readers;
Scheduled/Cancelled metadata is administrator-only, and Cancelled snapshot URLs are denied to all.
Future end dates are withheld from ordinary users until activation to avoid disclosing Scheduled
metadata. Current administrative ownership/export access stays outside snapshots.

Versions navigation, list, publish and cancel panels reuse the existing page/table/forms/side-panel
components. The publication review contains counts only, next number, UTC date, bounded release
note, stable Dataset UUID, expected revision and explicit confirmation. Cancellation retains its
number and snapshot and rechecks the activation boundary. Both support full-page no-JavaScript
submission. The shared controller exposes opt-in busy status and blocks duplicate submits; the
server remains authoritative. No design tokens, global CSS, variants or page-specific CSS were
added. Functional additions are the authorized version workflow, published reads and exports.
The shared form macro gained optional min/maxlength attributes. No design-system inconsistencies
were found in the reviewed views; human assistive-technology certification is not claimed.

All REF-82 formats use Current/Expired snapshot Fields, identifiers and canonical values, with
`<dataset-key>-v<number>` filenames. Administrator concept exports remain available. Existing
external sorting, spool cleanup and prepared-request audit semantics remain intact. Read/export
persistence failures are translated to neutral contracts and bounded 503 browser responses.
An already prepared authorized export can finish after revocation/deletion; subsequent requests
reauthorize. Imports and manual mutations only affect the concept. Stale import confirmation is
rejected after relevant revision changes; a Scheduled snapshot survives later import.

The complete migration and REF-141 deletion checks remain part of final regression. Four-table
constraint parity, all supported empty upgrade starting revisions, valid populated upgrades,
all-invalid preflight, failure rollback, retry and System publisher are covered on both engines.
Historical migrations are unchanged. Two obsolete private read helpers and obsolete web unpacking
were removed when the new coherent read path replaced them; no adjacent refactoring was performed.

## Part 2 validation and review ledger

The first full integration run found 59 failures (2,410 passed, three warnings). Most existing
reader fixtures had no publication, so the new Current-only rule correctly denied them. Fixtures
now explicitly publish valid content for ordinary-read tests. Deliberately unpublishable cases
assert non-disclosing 404/no concept exposure. Historical order tests prove concept reorder does
not reorder the published view. No production validation, authorization or historical migration
was relaxed. Fault injection now targets the coherent read boundary; response-failure assertions
require neutral 503 plus preserved artifact cleanup and prepared-request audit. The route inventory
guard now keys by endpoint AND path, checking every historical route rather than collapsing them.

- Dataset browser regressions: **138 passed**, no failures/skips/warnings, 24.88 seconds.
- Audit catalog/guard and side-panel regressions: **56 passed**, no failures/skips/warnings.
- Focused service/browser/access/governance/export/context/JS regressions: **937 passed**,
  no failures/skips, two existing SQLite datetime warnings, 153.11 seconds.
- Additional historical read tests initially found four test-setup/budget errors (179 passed):
  deleting a Field with remaining concept values is correctly prohibited; the fixture now deletes
  those concept Records first. The measured fixed read budget is 14 SELECTs including all three
  current capability checks; history is 10. Tests assert exact budgets and unchanged query counts
  after increasing versions/Records, not an unsubstantiated smaller estimate.
- Final focused `tests/test_dataset_version_views.py`: **48 passed**, no failures/skips/warnings,
  17.36 seconds. Includes 52-row deterministic pagination, live source deletion/key reuse,
  unchanged snapshot exports, actor disable/delete, current export revocation, cross-Dataset
  version IDs, neutral database-read failures and no success event on failure.

### Final verification

PostgreSQL commands use `REFRNCR_TEST_POSTGRES_URL` configured to the disposable local
PostgreSQL **16.15** database. SQLite is **3.51.0**, Python **3.12**. The full PostgreSQL-enabled
suite includes all SQLite tests and every PostgreSQL integration case.

| Command | Passed | Failed | Skipped | Other / warnings |
| --- | ---: | ---: | ---: | --- |
| `.venv/bin/python -m pytest -q --tb=short` with PostgreSQL 16 configured | 2,483 | 0 | 0 | 3 existing warnings; 771.16 s |
| `env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short --deselect=tests/test_database.py::test_postgresql_database_connection` | 1,785 | 0 | 697 | 1 PostgreSQL-only test deselected; 3 existing warnings; 396.93 s |
| `.venv/bin/python -m pytest -q --tb=short tests/test_dataset_versions.py tests/test_dataset_version_views.py tests/test_dataset_version_migration.py tests/test_audit_catalog.py tests/test_dataset_browser.py tests/test_side_panel_javascript.py tests/test_import_wizard_javascript.py` with PostgreSQL 16 configured | 400 | 0 | 0 | No warnings; 193.77 s |
| `.venv/bin/python -m ruff check .` | Pass | 0 | — | No findings |
| `.venv/bin/python -m ruff format --check .` | Pass | 0 | — | 136 files formatted |
| `.venv/bin/python -m pre_commit run --all-files` | Pass | 0 | 0 | All configured hooks |
| `.venv/bin/python -m pre_commit run --files <all 17 new files from the explicit list below>` | Pass | 0 | — | YAML/TOML hooks have no applicable new files; no staging |
| `node --check <file>` for every `src/refrncr/static/js/*.js` and `tests/javascript/*.cjs` | 18 files | 0 | 0 | No syntax errors |
| `git diff --check` | Pass | 0 | 0 | No whitespace errors |

The standalone SQLite command initially ran without socket permission: 1,783 passed,
697 pre-existing PostgreSQL fixture skips, three failures and three warnings. Two failures were
existing packaged-CLI tests binding loopback sockets; the other was the legacy unconditional
PostgreSQL connection smoke test, which falls back to localhost:5432 when its environment
variable is absent. The corrected SQLite run permits local sockets and selects every test
except that PostgreSQL-only smoke test. All 697 existing fixture skips AND the single explicitly
excluded PostgreSQL connection test execute successfully in the unfiltered PostgreSQL 16 run.
No skip, weakened assertion or removed regression was introduced into the tests to obtain green.
The two database configurations together cover all 2,483 collected tests without omissions.

The final 400-test focused run also covers the last cleanup of obsolete private helpers/web
unpacking after full-suite collection. No further production behavior changed after validation.
The three full-suite warnings remain Starlette/httpx deprecation (one) and Python 3.12 SQLite
datetime-adapter deprecation (two); no dependency/tooling changes were made to suppress them.

Reproduction of the 937-test integration run:

```sh
.venv/bin/python -m pytest -q --tb=short \
  tests/test_dataset_access.py tests/test_dataset_fields.py \
  tests/test_dataset_business_keys.py tests/test_dataset_records.py \
  tests/test_dataset_export.py tests/test_dataset_context.py \
  tests/test_dataset_governance.py tests/test_dataset_governance_review.py \
  tests/test_configure_overview.py tests/test_audit_coverage.py \
  tests/test_dataset_version_views.py tests/test_import_wizard_javascript.py
```

The new historical-read cases were added afterwards, so rerunning that earlier command now
collects more cases. Final migration/preflight tests are included in both the 400-test focused
run and the complete suite. No historical migration was edited.

### Real Chrome

The requested in-app browser skill was read and its documented bootstrap attempted. The runtime
reported `Browser not available: iab`. Validation used the repository's existing external
Playwright convention with actual **Google Chrome 152.0.7977.83**, headless light mode, disposable
loopback apps and screenshots outside the repository. No browser dependency was added.

Commands use `REFRNCR_PLAYWRIGHT_MODULE`, `REFRNCR_BROWSER_EXECUTABLE` and optional
`REFRNCR_BROWSER_OUTPUT` supplied externally; no machine-specific paths are stored here.

| Command | Target / viewports | Result |
| --- | --- | --- |
| `node tests/javascript/dataset_versions_browser.cjs` | `http://127.0.0.1:5183`; 320, 768, 1440 | 7 groups / 47 named scenarios passed |
| `node tests/javascript/table_rows_browser.cjs` | `http://127.0.0.1:5144`; 320, 768, 1440 | 18 groups passed; zero JS errors |
| `node tests/javascript/navigation_browser.cjs` | `http://127.0.0.1:5144`; 320, 767, 768, 1023, 1024, 1440 | Passed; zero JS errors |
| `node tests/javascript/dataset_governance_browser.cjs` | `http://127.0.0.1:5144`; 320, 768, 1440 | Passed; stale ownership, grant/revoke, focus, no overflow |
| `node tests/javascript/export_access_browser.cjs` | `http://127.0.0.1:5144`; 320, 768, 1440, 720×450 and 200% text | Passed; no-JS validation, no overflow/JS errors |
| `node tests/javascript/field_datatypes_browser.cjs` | `http://127.0.0.1:5146`; desktop and 320 | Nine datatypes, canonical edits/no-op, forged Float POST, no-JS passed |
| `node tests/javascript/audit_history_browser.cjs` | `http://127.0.0.1:5145`; desktop, 390 and 1280 at 200% CSS zoom | Six timezones, privacy, history, filters, no-JS and failure fallback passed |
| `node tests/javascript/dataset_creation_browser.cjs` | `http://127.0.0.1:5144`; 320, 768, 1440 | Creation and current maintenance matrix; unpublished reader denial |
| `node tests/javascript/dataset_deletion_browser.cjs` | `http://127.0.0.1:5144`; 320, 768, 1440 | Passed on a Dataset with a published snapshot |

Start fixture commands are `.venv/bin/python tests/browser_dataset_versions_app.py`,
`.venv/bin/python tests/browser_table_rows_app.py` and
`.venv/bin/python tests/browser_field_datatypes_app.py` and
`.venv/bin/python tests/browser_audit_history_app.py`. Mutating suites use fresh fixtures
where their setup requires it. Navigation additionally uses external browser URL/email/password
variables identifying only the disposable administrator.

REF-83 checks cover keyboard opening, initial focus, Tab containment, Escape/Cancel/focus
restoration, error focus, native/server date rules, busy/duplicate-submit behavior, escaped note,
Scheduled read-only/cancellation/number retention, Current-only ordinary reads, Expired record
panels, all three Current AND Expired downloads, concept-only CSV import preserving Scheduled
content, and no-JavaScript publication. Publication screenshots initially captured the entering
animation; tests now await animation completion and assert dialog bounds inside the viewport.
Representative 320/768/1440 screenshots were visually reviewed. The final REF-83 run also
uses a full 2,000-character release note and asserts no page JavaScript errors. An existing table test initially
checked focus before the queued native close event completed; its synchronization now waits for
that event before the unchanged exact-opener focus assertion. The full 18 groups then passed.
The existing creation browser matrix initially tried to open an unpublished Dataset as an
ordinary reader. It now explicitly asserts that 404 boundary and exercises its original
read/maintenance controls against the fixture's published Dataset.

## Impact and remaining limits

- Authorization: REF-73 only, with the explicit product-owner clarification recorded above.
  Current permission checks cover direct services and browser routes; no separate role or
  historical Grant reactivation. Session/CSRF/escaping/non-disclosing behavior is retained.
- Database/migrations: four full-snapshot tables plus Dataset revision in new migration 0015.
  All writers must stop during preflight/upgrade; populated rollback requires the matching backup.
- API: browser HTML routes only. Confluence records the future version/date contract, without
  implementing REF-107/108 or changing API authentication. No tenant fields or Cloud infrastructure.
- Audit: two additional central contracts, atomic publication/cancellation, safe bounded context,
  historical name fallback, complete inventory/matrix/guards. REF-141 audit history remains retained.
- Dependencies: none added or changed. Existing SQLAlchemy/Alembic and serializers are reused.
- Performance: full-copy storage and linear publication/compare memory; synchronous writer lock.
  No new Dataset cap or load/latency guarantee. Record paging retains in-memory canonical sorting;
  version history loads metadata for every version. Query counts stay independent of row count.
  Export retains bounded sorting/spooling. Privileged raw SQL is outside the immutability claim.
- Documentation: ADR-014, version/upgrade guide, governance, context, UI foundation, export/import,
  audit policy/inventory/coverage and README updated; Confluence architecture/security/deployment/API
  pages record the decision and implementation boundary. Local checks do not replace GitHub CI.
- Out of scope: REF-84/86/87/88/107/108/155/156, detailed diffs, background jobs, past-effective
  publication, republication, restore/discard, parallel Scheduled versions, retention and Dark Mode.

## Confluence traceability

The implementation/authorization decision is recorded in these existing pages, preserving
their preceding content and identifying REF-83 as local work, not a release or API deployment:

- [Architecture Overview & Domain Model](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41877505), version 14.
- [Deployment & Persistence](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41910273), version 4.
- [Security Architecture](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41418774), version 10.
- [API Architecture & Effective Dating](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41943041), version 3 (part 1 first recorded version 2).

Jira REF-83 remains the scope reference. No Jira completion transition, PR creation or CI run
is claimed for this uncommitted branch.

## Git handoff

Branch: `feat/ref-83-dataset-versioning`, based on safely updated `main`
`85be37fa3094ba74531b6130e4f0bbb2ce18fdcc` (HEAD/main/origin/main agree). No commit, push,
merge, release, staging or Jira completion transition is performed. The full explicit file list
below is also the changed-file inventory. CI must pass before a later authorized squash merge.

Suggested Conventional Commit: `feat: publish immutable Dataset versions (REF-83)`.
Suggested push, only after an explicitly authorized commit:
`git push -u origin feat/ref-83-dataset-versioning`.

Explicit recommended staging list (66 files; command not executed):

```sh
git add \
  README.md \
  docs/architecture/adr-014-dataset-versions.md \
  docs/development/audit-coverage.json \
  docs/development/audit-inventory.json \
  docs/development/audit-policy.md \
  docs/development/dataset-context.md \
  docs/development/dataset-export-security.md \
  docs/development/dataset-governance.md \
  docs/development/dataset-versions.md \
  docs/development/record-import-security.md \
  docs/development/ref-83-report.md \
  docs/development/ui-foundation.md \
  src/refrncr/app.py \
  src/refrncr/audit.py \
  src/refrncr/audit_guard.py \
  src/refrncr/audit_presentation.py \
  src/refrncr/cli.py \
  src/refrncr/dataset_export.py \
  src/refrncr/dataset_versions.py \
  src/refrncr/migrations/versions/0015_ref_83_dataset_versions.py \
  src/refrncr/models.py \
  src/refrncr/persistence.py \
  src/refrncr/ports.py \
  src/refrncr/record_import.py \
  src/refrncr/services.py \
  src/refrncr/static/js/refrncr.js \
  src/refrncr/templates/components.html \
  src/refrncr/templates/pages/datasets/_record_table.html \
  src/refrncr/templates/pages/datasets/detail.html \
  src/refrncr/templates/pages/datasets/list.html \
  src/refrncr/templates/pages/datasets/records.html \
  src/refrncr/templates/pages/datasets/versions/_navigation.html \
  src/refrncr/templates/pages/datasets/versions/cancel.html \
  src/refrncr/templates/pages/datasets/versions/list.html \
  src/refrncr/templates/pages/datasets/versions/publish.html \
  src/refrncr/version_contracts.py \
  src/refrncr/version_persistence.py \
  src/refrncr/version_upgrade.py \
  src/refrncr/web.py \
  tests/browser_dataset_versions_app.py \
  tests/browser_table_rows_app.py \
  tests/dataset_helpers.py \
  tests/javascript/dataset_creation_browser.cjs \
  tests/javascript/dataset_versions_browser.cjs \
  tests/javascript/import_wizard.cjs \
  tests/javascript/table_rows_browser.cjs \
  tests/test_audit_catalog.py \
  tests/test_audit_coverage.py \
  tests/test_audit_history.py \
  tests/test_configure_overview.py \
  tests/test_dataset_access.py \
  tests/test_dataset_browser.py \
  tests/test_dataset_business_keys.py \
  tests/test_dataset_context.py \
  tests/test_dataset_deletion.py \
  tests/test_dataset_export.py \
  tests/test_dataset_fields.py \
  tests/test_dataset_governance.py \
  tests/test_dataset_governance_migration.py \
  tests/test_dataset_governance_review.py \
  tests/test_dataset_records.py \
  tests/test_dataset_version_migration.py \
  tests/test_dataset_version_views.py \
  tests/test_dataset_versions.py \
  tests/test_field_datatype_migration.py \
  tests/test_migrations.py
```
