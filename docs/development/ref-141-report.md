# REF-141 — Dataset deletion implementation and verification

Status: technically ready for commit; all required local checks passed. Nothing staged,
committed or pushed. GitHub CI remains pending before merge.

## Scope and evidence

Community/core, based on the current REF-141, REF-73, REF-131, REF-142, REF-149 and
REF-39 contracts. Branch: `feat/ref-141-delete-dataset`, from updated main `d11a703`.
REF-141 explicitly authorizes the bounded Dataset-name snapshot exception to the
previous no-name audit policy. Technical object type remains the existing `dataset`;
the human-readable type is Dataset. No historical contract or audit row is rewritten.

## Dependency inventory before implementation

Evidence: all current models in `src/refrncr/models.py` and the complete migration
chain `0001_ref_33` through `0014_ref_145` (including migration environment).

| Dependency | Storage / constraints | Deletion disposition |
| --- | --- | --- |
| Dataset metadata, immutable key, creator and owning-team references, timestamps | `datasets`; creator and team FKs RESTRICT their targets | Delete Dataset last; preserve users, teams and memberships |
| Field definitions and display order | `dataset_fields`; Dataset FK CASCADE; unique Dataset/key and Dataset/position | Explicitly delete after values and identifier membership |
| Record identifier definition | `dataset_business_key_fields`; composite Field FK CASCADE | Explicitly delete before Fields |
| Records, identifier bytes and revision | `dataset_records`; Dataset FK CASCADE | Explicitly delete after values |
| Typed values and uniqueness bytes | `dataset_record_values`; Record FK CASCADE, Field FK RESTRICT | Explicitly delete first |
| Legacy user/team Grants | `grants`; polymorphic resource reference, no Dataset FK; inert under REF-73 | Delete only rows for resource type `dataset` and this UUID |
| Explicit user/team export capability | `dataset_export_access`; Dataset/user/team FKs CASCADE | Explicitly delete this Dataset's entries; preserve subjects |
| Existing Dataset/Field/Record/Grant/export-access audit | `audit_events`; object IDs have no target FK; actor FK RESTRICT | Preserve every historical row; append one `dataset.deleted` |
| Import upload, mapping, preview, confirmation and completion receipt | `TransientImportStore`, bounded process memory, actor/session/Dataset-ID binding, 15-minute TTL | Missing Dataset rejects use; existing lazy TTL cleanup removes state; no staged filesystem upload exists |
| Export status and temporary files | No status table; prepared spool plus temporary sort runs | New requests fail after deletion; existing coherent snapshot/prepared artifacts retain lifetime and cleanup semantics |
| Locks / revision data | Dataset row lock on PostgreSQL; SQLite BEGIN IMMEDIATE; Record revision is a column | Release transaction locks normally; no lock or revision-history table exists |
| Other Dataset foreign keys | None in current models or migration chain | No speculative cascade or future product tables |

Controlled order: values → Records → identifier membership → Fields → export access →
legacy Dataset Grants → Dataset. All SQL and the single audit insert share one write
Unit of Work. No child-object deletion events are invented. A failure rolls back the
complete operation, including audit. Existing DB cascades remain defensive constraints;
the application does not rely on unspecified cascade ordering across RESTRICT edges.

No migration is needed: no stored column, constraint or historical value changes.
The existing audit JSON can hold the bounded snapshot. SQLite foreign-key enforcement
and PostgreSQL constraints are verified against the migrated schema by the new tests.

## Confirmation and authorization contracts

Authorization reuses Dataset maintenance: active owning-team member or active Community
administrator. Historical roles, creator identity and export-only access confer no right.
Missing and denied targets use the same not-found contract before confirmation validation.

Confirmation compares Python strings exactly, case-sensitively, without trimming,
normalization, locale or casefold. The expected UTC metadata timestamp detects changed
state without adding a general revision column. The route uses the stable Dataset UUID,
so key reuse cannot redirect an old confirmation to a replacement Dataset. Browser checks
are supportive; every service invocation checks current authorization and name again.

Import confirmation must recheck its claimed Dataset UUID under the write lock, including
when a key has been deleted and reused. New exports fail when the Dataset no longer exists;
already started coherent snapshots retain ADR-012 semantics. Transient cleanup is not a
database mutation and must never be reported as one.

## Implemented boundaries

`DatasetService.delete_dataset` is the sole deletion authority. Its confirmation read
returns only identity, current name, counts and the existing metadata timestamp. The new
GET/POST `/datasets/id/<dataset_id>/delete` browser route supplies trusted session actor,
UUID and singleton confirmation/revision fields; GET has no mutation. POST requires the
existing CSRF check. Malformed, missing and denied targets return the same 404 before name
validation. A valid actor sees 400 for a mismatch and 409 for stale/conflicting state.
Persistence adapters translate database failures to the existing neutral conflict error;
unavailable confirmation reads become neutral 503. No SQLAlchemy imports enter the service
or route. Error responses reload authorized context and do not echo submitted names.

The Unit of Work factory adds an opt-in `translate_errors` option for this boundary,
covering transaction start, body/SQL, flush and commit errors. Existing callers retain their
historical exception contracts. Deletion failure and audit-insert failure restore all
original rows, timestamps and audit history. A successful request writes one event, even
when the Dataset contains many Fields, Records, identifiers, access rows and legacy Grants.

The stable contract is object type `dataset` (presented as Dataset), action
`dataset.deleted`, trusted actor ID and Dataset UUID, with exactly one context key:
`dataset_name`. The central validator accepts at most 200 codepoints before processing,
removes control/format/surrogate characters and uses a neutral fallback if no displayable
name remains. No descriptions, values, Field content, subject lists, upload/export content
or request/exception payload are collected. Counts appear in confirmation/result only.
List and detail use the same validated snapshot and **name (deleted)** label with no current
Dataset link. Their SQL projection retains the 4096-character pre-parsing bound; list reads
only fetch context for this event. Historical rows and all other fallback rules remain intact.

Two safety changes are required by the inventory: import confirmation checks its original
claimed UUID again after locking, and legacy Dataset-Grant creation locks/checks the Dataset
before the actor. Otherwise, a claimed import could follow a recreated key or a concurrent
legacy Grant could survive without a Dataset foreign key. Other resource Grants retain
existing behavior. No speculative interfaces, dependency, background worker or Cloud fields
are added.

PostgreSQL serializes writers on the Dataset row; SQLite uses `BEGIN IMMEDIATE`. Metadata,
Field/Record and import writes obey the same ordering. If deletion wins, subsequent writes
fail closed; if a mutation wins and changes the timestamp, the old confirmation is stale.
Current authorization is always rechecked under the transaction, independent of the hidden
timestamp. Concurrent deletion/replay yields exactly one committed removal and event.
Existing export snapshots and prepared artifacts retain ADR-012 semantics. A new export
cannot resolve a deleted Dataset. Old imports cannot create records after deletion or attach
themselves to a new Dataset with the reused key.

## UI changes and consistency review

- Design tokens: unchanged; existing spacing, border, warning and danger values are reused.
- Shared component: remote side panels support one explicit parent-to-confirmation transition
  through `data-panel-next`, preserving the actual parent DOM, unsaved edits and triggering
  element. There is one modal, with no nested dialogs. Late responses are invalidated.
- Component variants: `.danger-zone` is a single shared border/spacing rule;
  `data-exact-confirmation` adds supportive validity/disabled-state behavior to forms.
- Deliberate workflow markup: the Dataset confirmation template supplies permanent warning,
  name/counts, labelled/help-linked required input, error summary, Cancel and destructive submit.
  It uses existing macros. The edit danger action is separate from the normal edit submit.
- Functional change: authorized permanent deletion. Success uses 303 and shared status feedback
  on the Dataset list. The fixed `deleted=1` marker is cosmetic, not proof of deletion, and does
  not introduce a Flask session or alter existing authentication cookies. Back/Refresh cannot
  replay the POST. No-JavaScript navigation retains the normal full-page fallback.

Cancel, Escape and header close restore the edit view and exact delete trigger; closing that
view restores the original Edit dataset opener. Validation focuses the shared error summary
and links the input to the error. Real Chrome verified native dialog keyboard containment,
accessible roles/names, focus restoration, light rendering and no viewport overflow at
320, 768 and 1440 px. Screenshots at each size were visually reviewed. No new token duplicates,
page-specific CSS, composite Python markup, dark theme or framework were introduced.

## Tests added or extended

`tests/test_dataset_deletion.py` covers real SQLite/PostgreSQL persistence for empty,
Fields-only and populated Datasets; complete dependency deletion with historical/unrelated
row preservation; all required authorization classes; exact case/whitespace/NFC-NFD/control
comparisons; stale rename/rename-back/metadata/Field/Record cases; five rollback stages;
coordinated concurrent delete, mutation, import, legacy Grant and snapshot export; key reuse;
TTL/replay; bounded hostile audit names; list/detail fallback; CSRF, duplicate fields, 303,
Back/Refresh and uniform IDOR responses. Concurrency uses barriers/events, not sleeps.

The migration test runs the complete real chain through `0014_ref_145` on each engine,
verifies an invalid FK and the Field-value RESTRICT edge fail, then verifies explicit deletion
and historical audit retention. SQLite additionally checks `PRAGMA foreign_keys=1` and an
empty `foreign_key_check`. No migration is required: columns, constraints and stored historical
data are unchanged, and the existing JSON context supports the bounded snapshot.

The existing audit mutation matrix now exercises deletion success, rejection, rollback and
retry. Catalog/history/object-presentation tests include the new contract and its bounded list
projection. Shared JavaScript tests cover parent restoration and late responses. The new
real-Chrome script tests keyboard interaction, duplicate submission, stale state changed in
another tab, deliberately bypassed browser validation, escaped hostile names in confirmation
and audit, reader visibility and redirect history. Existing service, web, access, Field/Record,
import/export, migration and preflight checks are included in the full pytest suites.

## Validation commands and results

| Check | Passed | Failed | Skipped | Warnings / evidence |
| --- | ---: | ---: | ---: | --- |
| Full suite with PostgreSQL 16.15 enabled (includes SQLite cases) | 2245 | 0 | 0 | 3 existing deprecations; 736.86 s |
| Standalone SQLite configuration | 1667 | 0 | 578 | 3 existing deprecations; 363.41 s |
| Focused REF-141, SQLite and PostgreSQL 16.15 | 101 | 0 | 0 | 0 warnings; 58.78 s |
| Audit catalog/inventory/coverage guard after documentation updates | 55 | 0 | 0 | 0 warnings; 22.80 s |
| Real Chrome regression scripts | 6 scripts | 0 | 0 | See scenarios below |
| Node side-panel controller | Passed | 0 | 0 | Parent restoration and existing regressions |
| JavaScript syntax | 17 files | 0 | 0 | `node --check` |
| Ruff lint and format check | Passed | 0 | 0 | 124 Python files already formatted |
| pre-commit, all tracked files | 7 hooks | 0 | 0 | All configured hooks passed |
| pre-commit, four new files explicitly | 5 applicable hooks | 0 | 2 N/A | YAML/TOML hooks have no matching files |
| `git diff --check` | Passed | 0 | 0 | No whitespace errors |

The existing full-suite warnings are one Starlette/httpx test-client deprecation and two
Python-3.12 SQLite datetime-adapter deprecations in the populated REF-75 upgrade test.
No warning suppression or new dependency was added. PostgreSQL fixture skips in the standalone
configuration are existing conditional coverage; every case runs in the enabled full suite.
GitHub Actions cannot run for this unpushed change and remains the independent pre-merge gate.

Environment: repository `.venv`, Python 3.12.2, SQLite 3.51.0, PostgreSQL **16.15**
(Debian 16.15-1.pgdg13+2), Node 24.9.0, real Chrome 152.0.7977.83. The PostgreSQL-16
run uses a disposable `postgres:16` container on loopback port 55416. Its version was queried
from that actual SQL connection. Port 5432 on this workstation is PostgreSQL 15.18; preliminary
runs there are not PostgreSQL-16 evidence. The standalone SQLite configuration still includes
the repository's unconditional PostgreSQL connection smoke test on that default port.

```sh
# Temporary test-only server; credentials are disposable and loopback-only.
docker run --detach --rm --name refrncr-ref141-pg16 \
  --publish 127.0.0.1:55416:5432 \
  --env POSTGRES_USER=refrncr --env POSTGRES_PASSWORD=refrncr \
  --env POSTGRES_DB=refrncr_test postgres:16

REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55416/refrncr_test \
  .venv/bin/python -m pytest -q --tb=short

env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short

REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@127.0.0.1:55416/refrncr_test \
  .venv/bin/python -m pytest -q tests/test_dataset_deletion.py --tb=short
.venv/bin/python -m pytest -q tests/test_audit_catalog.py --tb=short

.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m pre_commit run --files \
  docs/development/ref-141-report.md \
  src/refrncr/templates/pages/datasets/delete.html \
  tests/javascript/dataset_deletion_browser.cjs tests/test_dataset_deletion.py
node tests/javascript/side_panels.cjs src/refrncr/static/js/refrncr.js
git diff --check
```

JavaScript syntax was checked using `node --check` on each of the 17 files found by
`rg --files src/refrncr/static/js tests/javascript` with `.js`/`.cjs` suffixes.
Browser checks used the repository's existing external Playwright convention, without an
installed project dependency. Each row below starts a fresh `.venv/bin/python tests/<fixture>`
server, then runs `node tests/javascript/<script>` and stops that fixture. Standard output is
drained continuously so Flask cannot block on a full log pipe.

| Disposable fixture | Chrome script | Result |
| --- | --- | --- |
| `browser_table_rows_app.py` | `dataset_deletion_browser.cjs` | Passed, 320/768/1440 and actual stale/wrong-name/success/escaped-audit flows |
| `browser_table_rows_app.py` | `dataset_governance_browser.cjs` | Passed, owner/export panels, persistence, stale focus |
| `browser_table_rows_app.py` | `export_access_browser.cjs` | Passed, 320/768/1440, text zoom, no-JS validation |
| `browser_table_rows_app.py` | `audit_export_browser.cjs` | Passed, real CSV/Excel/JSON downloads and reader denial |
| `browser_field_datatypes_app.py` | `field_datatypes_browser.cjs` | Passed, all nine types, Record canonical/no-op edits, no-JS fallback |
| `browser_audit_history_app.py` | `audit_history_browser.cjs` | Passed, six timezones, keyboard/history/privacy/fallback/errors/zoom |

Executed browser environment:

```sh
REFRNCR_PLAYWRIGHT_MODULE=/private/tmp/ref134-browser/node_modules/playwright
REFRNCR_BROWSER_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
REFRNCR_BROWSER_OUTPUT=/private/tmp/ref141-browser
REFRNCR_SCREENSHOT_DIR=/private/tmp/ref141-browser
```

These are local test locations, never product output or audit context. Browser logs/screenshots
and temporary orchestration scripts remain outside the repository. The task-only PostgreSQL 16
container was stopped with `docker stop refrncr-ref141-pg16` after its suites passed; existing
local database services were preserved. Browser fixture servers were stopped by the harness. The Codex in-app browser was
attempted first but reported `Browser is not available: iab`; the documented real-browser fallback
was used. One field-type browser attempt timed out at Record opening; the fresh-fixture rerun with
continuous server-log draining passed unchanged. No product assertion was weakened.

Initial full runs exposed missing deletion cases/count/formatter expectations in existing audit
tests; these were corrected to assert the new contract. The first standalone SQLite attempt had
three sandbox-only failures (default PostgreSQL loopback plus two CLI socket-bind checks):
1664 passed, 578 skipped, 3 failed, 3 warnings. It was rerun with local network access; no tests
were skipped or modified to bypass the failures. A standalone Node-controller invocation
initially omitted its required source-file argument and failed before executing checks; the
correct command above passed. Preliminary PostgreSQL-15 runs are diagnostic
only; the final required engine is the separately verified PostgreSQL 16 instance.

## Impact and remaining limits

Authorization, CSRF, session identity, IDOR and audit minimization are preserved. Schema,
migrations, dependencies and Community tenancy are unchanged. There is one additional browser
route and a small service/port extension, with no new external API platform. The implementation
extends existing Unit of Work, locking, audit and panel patterns; no significant new architecture
requiring an ADR was introduced. Relevant governance, import/export, audit and UI guides are
updated; [Confluence Architecture Overview v13](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41877505/Architecture+Overview+Domain+Model)
and [Security Architecture v9](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41418774/Security+Architecture)
record the local REF-141 design. Jira numbering/status was not changed.

Permanent deletion has no undo, trash, soft-delete, recovery, bulk action or retention policy.
Already prepared/downloaded exports are not recalled. Inaccessible import bytes follow lazy
TTL/session cleanup rather than guaranteed immediate memory erasure. The existing `updated_at`
value is an optimistic timestamp token, not a new monotonic revision; the current stored name
and authorization are checked even if a client alters that token. Existing session/cursor and
export cleanup operational limits remain applicable. Audit is append-only by application contract,
not a new database tamper-proof or retention system. Browser role/DOM/keyboard checks are not a
claim of a manual screen-reader certification. GitHub CI is pending until explicitly authorized
commit/push; local green checks do not substitute for it.

## Changed files and handoff

Production: `services.py`, `ports.py`, `persistence.py`, `record_import.py`, `audit.py`,
`audit_presentation.py`, `web.py`, shared `refrncr.js`/`dialog.css`, Dataset edit/list templates
and the new delete template. Documentation: audit inventory/coverage/policy/history, Dataset
governance, import/export security, UI foundation and this report. Tests: new deletion service/web
and browser tests, shared side-panel tests, existing audit catalog/coverage/history/presentation.
All paths are listed by the explicit staging recommendation below. No temporary upload, browser
artifact, debug fixture data, new dependency or historical migration is included.

Branch: `feat/ref-141-delete-dataset`. No files staged, no commit or push performed. The final diff
is reviewed against current `main` (`d11a7037967d3219411f04275b26bb012817df8e`), confirmed
with `git ls-remote origin refs/heads/main`. Status contains exactly 24 modified tracked files
and four new files, with an empty index. GitHub CI and merge remain pending. Recommended commands, **not run**:

```sh
git add \
  docs/development/audit-coverage.json docs/development/audit-history.md \
  docs/development/audit-inventory.json docs/development/audit-policy.md \
  docs/development/dataset-export-security.md docs/development/dataset-governance.md \
  docs/development/record-import-security.md docs/development/ui-foundation.md \
  docs/development/ref-141-report.md \
  src/refrncr/audit.py src/refrncr/audit_presentation.py src/refrncr/persistence.py \
  src/refrncr/ports.py src/refrncr/record_import.py src/refrncr/services.py src/refrncr/web.py \
  src/refrncr/static/css/components/dialog.css src/refrncr/static/js/refrncr.js \
  src/refrncr/templates/pages/datasets/edit.html \
  src/refrncr/templates/pages/datasets/list.html \
  src/refrncr/templates/pages/datasets/delete.html \
  tests/javascript/side_panels.cjs tests/javascript/dataset_deletion_browser.cjs \
  tests/test_audit_catalog.py tests/test_audit_coverage.py tests/test_audit_history.py \
  tests/test_audit_object_presentation.py tests/test_dataset_deletion.py

git commit -m "feat(dataset): safely delete datasets (REF-141)"
git push -u origin feat/ref-141-delete-dataset
```
