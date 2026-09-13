# REF-155 — implementation notes, part 1

This is the historical implementation/targeted-test checkpoint. See the
[part-2 review and validation report](ref-155-report.md) for subsequent findings and results.
Full regression and real Chrome validation belong to part 2. Nothing is staged,
committed or pushed. Jira REF-155 contains the approved product clarification.

## Approved eligibility

Discard is available to current owning-team members and active Community admins
for an active Concept **only when a Current version exists**, including an
unchanged Concept. Legacy Viewer/Editor grants and export access are insufficient.
The service rechecks authority and Current at confirmation under the Dataset lock.
A first unpublished Concept or a Scheduled-only Dataset cannot be discarded.
The visible disabled action has a focusable `aria-disabled` control linked to an
always-visible explanation, readable by pointer, keyboard and assistive technology:

> This Concept contains the Dataset’s first unpublished version. There is no Current version to return to. Publish it first or delete the Dataset.

Without Current, no metadata or other business state changes. There is no fallback
name and no schema change to remember original metadata.

## Architecture and ownership inventory

`DatasetVersionService` owns authorized review and discard. `ConceptOperations`
adds one explicit deletion operation, implemented by the existing SQLAlchemy adapter.
The existing Unit of Work owns commit/rollback. No parallel version, Record,
permission or workflow model is introduced. **No migration is needed.**

The Dataset parent contains mutable name/description plus stable ownership and
administrative identity. The active `dataset_concepts` row has its own UUID,
Dataset primary key, optional pinned snapshot, creator, start revision and timestamps.
Concept content lives in the existing working tables, not in a second snapshot.
Record Added/Changed/Deleted states are computed against the pinned baseline;
Deleted rows are baseline projections, not separate mutable tombstones.

Explicit child-first discard deletes:

1. `dataset_field_source_formats` for the working Fields;
2. `dataset_record_values`;
3. `dataset_records`, including their revisions;
4. `dataset_business_key_fields`;
5. `dataset_fields`, including definitions and display positions;
6. `dataset_concepts`, including its lifecycle identity and timestamps.

Source-format proposals reference working Field IDs with `ON DELETE CASCADE`.
Values have composite Dataset/Record and Dataset/Field foreign keys. Identifier
members reference Dataset/Field keys. Working Fields/Records and Concept reference
the Dataset. The Concept's optional composite baseline FK references a snapshot of
that same Dataset. Discard explicitly removes children instead of relying on cascades.

Dataset name and description are restored from Current, and content freshness uses
Current's publication timestamp. The Dataset revision advances once; it must remain
monotonic to reject stale requests. There is no separate mutable Concept revision
counter: content writers advance Dataset revision and touch Concept updated-at.
Confirmation captures both, plus the Concept's immutable start revision.

Preserved: Dataset UUID/key/creator, ownership, grants, export access, users, teams,
memberships, all existing audits, and every Current/Scheduled/historical/Cancelled
snapshot with its independent Field/Record/value rows. Snapshots reference stable
source UUIDs without FKs back to mutable Fields/Records. No replacement Concept is
created. Later explicit creation retains the existing latest-non-cancelled-baseline
contract, including Scheduled; this story does not change that creation policy.

## Confirmation, concurrency and transient state

The confirmation is signed using the existing review-token machinery with a
discard-specific HMAC key derivation. It binds Dataset UUID, Concept UUID,
Dataset revision, Concept start revision and updated-at, actor, browser session,
owning team and Current snapshot UUID. Review tokens cannot be used as discard
tokens. Exact form keys, one value each and CSRF are required.

Both review and confirmation recompute bounded summary counts using the existing
Field/Record classifier. Metadata count includes name/description; Field changes
include order and identifier membership. No Record values or Dataset descriptions
are rendered in the dialog or included in audit. A stale confirmation returns a
409 with current counts and a fresh signature for explicit confirmation.
Current identity is checked again after counting because calendar activation has
no revision write.

PostgreSQL uses the established Dataset → team → actor locks; SQLite uses
`BEGIN IMMEDIATE`. Record mutation, publish, import and discard serialize through
that same Dataset write boundary. The loser either sees changed revision or no
active Concept. Rollback retains all children, metadata, identity and audit history.
Replay cannot delete a replacement Concept because its UUID/start revision differ.

Staged imports are process-local bytes and immutable wizard state, with no upload
files or database rows. They now bind the original Concept UUID as well as actor,
session and Dataset. Every staged read checks that UUID; Confirm repeats the check
inside its write UoW. Old executable tokens and completed receipts cannot attach to
a new Concept. Successful HTTP discard purges that Concept's local store entries,
including busy claims. `finish()` cannot resurrect a purged claim. A direct service
caller or another process still has immediate logical invalidation through the
database; memory is purged on obsolete-state lookup/resume or existing lazy TTL/
session cleanup. No global memory-erasure or distributed transaction is claimed.

Diff/review links already bind Concept UUID and revision and fail after discard.
There is no persisted export job, download token or remembered browser Concept mode.
Concept export captures the Concept UUID in its read snapshot and rechecks identity
and maintenance authority under the Dataset lock before its prepared-request audit.
Discard winning that boundary closes the spool and returns no artifact/event.
An already accepted streaming export cannot be recalled; it follows normal spool
cleanup. Current and historical export retain their existing snapshot boundary.

## UI and accessibility

The shared Concept component exposes a destructive menu action on Concept pages
and Versions. It reuses the existing native-dialog sidepanel, buttons, counts and
feedback components. Disabled help is ordinary visible text, not a hover-only
custom tooltip. No new token, framework, dependency or JavaScript controller is added.
The deliberate menu variant bounds its width and wraps its help text.

The dialog has an accessible title and description, identifies Dataset and Concept,
explains permanent removal and unchanged snapshots, and initially focuses its
warning. Cancel/Escape, focus trap, error focus and double-submit handling use the
shared controller. Focus restoration now reopens the opener's native details menu.
The same confirmation is a full-page form without JavaScript. Successful POST uses
303 to the Dataset's Current overview, shows that Current was unchanged and offers
Create concept. Concept routes and confirmation responses are no-store.

## Performance and remaining part-2 work

Counts reuse existing SQL diff aggregates and Field classification; no Record/value
graph is fetched for the confirmation. Field definitions are materialized as in the
existing Concept summary. Writes are set-based per table, with no per-Record audit
or delete loop. Imports add a Concept lookup for their binding. Exact query counts,
large-data timing and layout measurements have not yet been recorded.

Part 2 must perform full SQLite/PostgreSQL 16 regression, package/CLI checks, real
Chrome at 320/768/1440 px, keyboard/focus and no-JavaScript flows, Back/Refresh,
console checks and overflow measurements. Test results below are targeted evidence,
not a claim of full regression, real-browser validation or green GitHub CI.

## Targeted validation

Python 3.12.2; SQLite 3.51.0; PostgreSQL 16.15
(`Debian 16.15-1.pgdg13+2`, existing local `postgres:16` test container).
The existing `REFRNCR_TEST_POSTGRES_URL` points at the disposable local test database;
these runs use isolated test schemas, not application data.

- Targeted version/import/export run: **441 passed**, no failures/skips, 96.88 s.
- Final directly affected lifecycle/UI/audit run: **208 passed**, no failures/skips,
  56.61 s. This overlaps the previous run; counts must not be added together.
- After strengthening the existing Field Added/Deleted fixtures, their directly
  affected scenarios passed again: **12 passed**, 64 deselected, 7.81 s, on both
  engines (`-k discard_removes_working_state`). These cases clear working values
  before deleting a Field, as the unchanged domain validator requires. Log:
  `/private/tmp/ref-155-part1-field-cases.txt`.
- The new `tests/test_concept_discard.py` contains **76 cases**, 38 for each engine.
- Ruff lint and format check passed; `git diff --check` passed.
- `python -m pre_commit run --all-files` passed. The three new untracked files
  were also checked explicitly with `--files`; format-specific hooks have no
  applicable YAML/TOML files in that small selection.
- JavaScript syntax check passed. Shared controller assertions cover warning-first
  focus, reopening the menu on focus restoration, existing cancellation/error paths
  and idempotent controller initialization. Real native Chrome behavior is deferred.

Commands, using `.venv/bin/python` and the test PostgreSQL URL in the environment:

```shell
python -m pytest tests/test_concept_discard.py tests/test_concept_lifecycle.py tests/test_import_wizard.py tests/test_import_wizard_javascript.py tests/test_side_panel_javascript.py tests/test_audit_catalog.py tests/test_dataset_version_views.py tests/test_dataset_versions.py tests/test_record_import.py tests/test_dataset_export.py -q --tb=short
python -m pytest tests/test_concept_discard.py tests/test_concept_lifecycle.py tests/test_import_wizard.py tests/test_import_wizard_javascript.py tests/test_side_panel_javascript.py tests/test_audit_catalog.py -q --tb=short
python -m ruff check .
python -m ruff format --check .
python -m pre_commit run --all-files
python -m pre_commit run --files tests/test_concept_discard.py src/refrncr/templates/pages/datasets/concepts/discard.html docs/development/ref-155-part-1-notes.md
git diff --check
node --check src/refrncr/static/js/refrncr.js
```

Logs are outside the repository at `/private/tmp/ref-155-part1-targeted.txt` and
`/private/tmp/ref-155-part1-final-focused.txt`. No Chrome screenshots/layout evidence
or final delivery report has been produced in part 1.

Tests exercise unchanged and changed Concepts; metadata, Field configuration/order/
identifier changes; mixed Added/Changed/Deleted Records; Current/Scheduled/Cancelled
snapshot preservation; governance preservation; owning-team/admin access and grant-only
denial; Dataset/Concept identity and revision mismatch; calendar activation; CSRF and
strict form keys; cross-session binding; replay before/after replacement Concept;
concurrent Record/publish/import/discard; failure after deletion stages/audit flush/
commit; import tokens and receipts; source-format cleanup; pending export invalidation;
exact safe audit; success redirect and accessible dialog markup.

## Changed files


- `docs/architecture/adr-012-audit-export-boundary.md`
- `docs/development/audit-coverage.json`
- `docs/development/audit-inventory.json`
- `docs/development/audit-policy.md`
- `docs/development/dataset-export-security.md`
- `docs/development/dataset-governance.md`
- `docs/development/dataset-versions.md`
- `docs/development/record-import-security.md`
- `docs/development/ref-155-part-1-notes.md`
- `docs/development/ui-foundation.md`
- `src/refrncr/audit.py`
- `src/refrncr/audit_presentation.py`
- `src/refrncr/concept_contracts.py`
- `src/refrncr/concept_persistence.py`
- `src/refrncr/dataset_versions.py`
- `src/refrncr/ports.py`
- `src/refrncr/record_import.py`
- `src/refrncr/review_tokens.py`
- `src/refrncr/services.py`
- `src/refrncr/static/css/layouts/page.css`
- `src/refrncr/static/js/refrncr.js`
- `src/refrncr/templates/components/concept.html`
- `src/refrncr/templates/pages/datasets/concepts/discard.html`
- `src/refrncr/templates/pages/datasets/detail.html`
- `src/refrncr/templates/pages/datasets/versions/list.html`
- `src/refrncr/web.py`
- `tests/javascript/side_panels.cjs`
- `tests/test_audit_catalog.py`
- `tests/test_concept_discard.py`
