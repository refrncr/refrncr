# REF-160 — complete new Record version imports

13 September 2026. Community/core. Implementation and delivery verification on
`feat/ref-160-import-new-version`, from updated `main` at `23cc9b8`.
Nothing staged, committed, pushed, merged or released. GitHub CI is pending because
this task explicitly prohibits pushing; it remains the independent pre-merge gate.

## Scope and decisions

[Jira REF-160](https://cleverhorizons.atlassian.net/browse/REF-160) is authoritative.
REF-155 is merged as `23cc9b8` / PR #53 and REF-159 as `5d5c808` / PR #52.
The remaining dependency context is REF-81, REF-134, REF-145, REF-154, REF-158,
REF-83/84, REF-73 and REF-149. Repository instructions, existing implementation,
tests and import/export/source-format/security/locking/audit/UI/version documentation
were reviewed. The resumed working tree was clean; no unrelated work was overwritten.

The existing working Record tables, canonical parsers, Concept lifecycle, ports,
Dataset write lock, Unit of Work and immutable published snapshots remain authoritative.
A single Import action stages the entire proposed file. The original import address and
REF-160 address now both select full comparison, including on a first empty Concept.
The Python service retains its legacy create-only mode for existing programmatic callers;
the product exposes no mode choice or append-only entry. It requires every active Field,
complete canonical identifiers and at least one valid Record. Full comparison classifies
Added, Changed, Unchanged, Deleted and Error. Changed identifiers become Deleted plus
Added. Preview shows full counts, 50 differences per page, complete-identifier search,
status filtering and old/new changed Field values. Display filters never exclude writes.

Confirmation repeats authority, Concept identity, eligibility, revisions, schema and
canonical difference under the Dataset lock. Deleted Records require an unchecked,
exact-count acknowledgement tied to the current opaque confirmation. Added/Changed/
Deleted apply atomically; Unchanged never receives a write or revision increment.
The successful one-use marker and one safe audit summary commit with the content.
Competing staged imports cannot both succeed. Success opens the Concept table filtered
to changes, with totals, identifier search and a checkbox status menu. All records in
that menu clears the filter and preserves the identifier query.

Every deleted Record retains its UUID and last Concept values and offers Undo delete.
The normalized active-value projection is cleared; the same Record row retains canonical
values and Field metadata, so deleted content survives later Field removal/retyping.
Restore checks Concept identity, Record revision, authority, current schema and uniqueness.
Publication excludes deleted content and preserves Current/history through existing
immutable snapshots. Creating a later Concept resets retained deletions from its baseline.

Two product-owner decisions resolve irrecoverable historical state:

1. Restore old physically deleted baseline Records as Deleted using their published
   baseline values. Their last working Concept values no longer exist. New deletions
   retain actual last Concept values.
2. Make **every** pre-migration Concept ineligible for new-version imports, with the exact
   supplied explanation and publish/discard/new-Concept recovery path. Do not reconstruct
   Dataset IDs in historical Record audits. New Concepts begin with a reliable false
   mutation marker; only Field changes do not block them. This is a one-time compatibility
   boundary, not an error. Later mutation of a legacy Concept keeps that classification.

[ADR-016](../architecture/adr-016-complete-record-import.md) records the design and
alternatives. A matching [Confluence architecture draft](https://cleverhorizons.atlassian.net/wiki/pages/resumedraft.action?draftId=50921473&draftShareId=57de1041-7310-4b0d-bad1-7122951d71a3)
was created beneath Architecture Overview & Domain Model and read back to verify its
draft status and compatibility-boundary text. It explicitly describes local uncommitted
work, without claiming a deployment or release.

## Database and API impact

Alembic `0018_ref_160` follows `0017_ref_154`. It adds nullable
`dataset_concepts.records_mutated`, `dataset_concepts.import_result` and
`dataset_records.deleted_content`, and replaces identifier uniqueness with an active-row
partial unique index under the same constraint name. There is no second Record model.
Migration backfills retained baseline deletions and unknown legacy eligibility atomically
on SQLite/PostgreSQL. Real injected migration-copy failures verify schema/data/revision
rollback and retry. Historical publication/Concept upgrade adapters use explicit old
schema projections; migration fixtures preserve all historical value/audit assertions.

Use the established stopped-writer backup and Alembic process. Downgrade fails closed
when retained deletions or a completed active import cannot be represented by old code.
No migration was applied to a user's application database; all execution used disposable
fixtures or isolated PostgreSQL schemas.

Two browser routes extend the existing protected interface:

- `GET,POST /datasets/<key>/concept/records/import-new-version`
- `POST /datasets/<key>/concept/records/<record_id>/restore`

Existing token-bound wizard routes carry the staged mode. There is no REST import API,
new role, dependency, tenant isolation layer or Cloud infrastructure. Authorization
continues to use current owning-team/Community-administrator capability. CSRF, uniform
missing/denied targets, escaping, TTL, upload/archive limits and session binding remain.

Audit adds `dataset:dataset.new_version_imported` with mode, format, Added/Changed/
Unchanged/Deleted counts and `total_processed` (source Records plus Deleted Records), and
`dataset_record:restored`. Both are transactional. No source rows, values, identifiers,
filenames or examples enter these events. The catalog, safe presentation, coverage matrix
and interface inventory were updated together.

## Changed components

| Area | Files |
| --- | --- |
| Domain/contracts | `models.py`, `ports.py`, `concept_contracts.py`, `version_contracts.py` |
| Persistence/lifecycle | `persistence.py`, `concept_persistence.py`, `diff_persistence.py`, `dataset_versions.py`, `services.py` |
| Upgrade compatibility | `migrations/versions/0018_ref_160_new_version.py`, `concept_upgrade.py`, `version_upgrade.py`, `cli.py` |
| Import/security | `record_import.py`, `web.py` |
| Audit | `audit.py`, `audit_presentation.py`, `docs/development/audit-coverage.json`, `audit-inventory.json`, `audit-policy.md` |
| UI | `templates/components/import_action.html`; Dataset `records.html`, `_record_table.html`, `_record_panels.html`; import `wizard.html`, `upload.html`, `preview.html`, new `differences.html`, `unavailable.html`; new `records/restore_error.html`; `static/css/layouts/import.css` |
| New verification | `tests/test_new_version_import.py`, `tests/browser_new_version_app.py`, `tests/javascript/new_version_browser.cjs` |
| Regression fixtures/assertions | `tests/dataset_helpers.py`; `test_audit_catalog.py`, `test_audit_coverage.py`, `test_audit_history.py`, `test_audit_object_presentation.py`, `test_concept_migration.py`, `test_dataset_browser.py`, `test_dataset_deletion.py`, `test_dataset_records.py`, `test_dataset_version_migration.py`, `test_field_datatype_migration.py`, `test_migrations.py`, `test_source_format_transactions.py` |
| Documentation | ADR-016, `dataset-versions.md`, `record-import-security.md`, `ui-foundation.md`, this report and the linked Confluence draft |

Python/template paths without a prefix are under `src/refrncr`. No configured quality
check was weakened or dependency added. Old physical-delete assertions now assert retained
content, while preserving normalized-value cleanup, audit and history assertions.

## Verification

Environment: Python **3.12.2**, pytest **8.4.2**, Ruff **0.16.5**, SQLite **3.51.0**,
PostgreSQL **16.15**, real Chrome **152.0.7977.84**. PostgreSQL was enabled with the
repository's disposable local test URL; database credentials are test-only. Network/socket
sandbox restrictions initially blocked local connection/CLI checks; authorized reruns
used the same tests. Early regressions in route registration, audit inventory and
historical-schema assumptions were investigated and fixed before delivery.

Required commands (using `.venv/bin/python`):

```shell
python -m pre_commit run --all-files
python -m pytest -q --tb=short
python -m ruff check .
```

The complete pytest run uses `REFRNCR_TEST_POSTGRES_URL` pointing to disposable PostgreSQL
16. New unstaged files are also checked with the configured pre-commit hooks via `--files`;
`--all-files` alone would omit them. `git diff --check` is clean.

The full-suite run after unifying the Import entry point completed with **3,302 passed
and 7 failed**, with 3 existing deprecation warnings, in 656.54 seconds. Those seven
failures were obsolete wizard UI assertions and a test fixture that omitted the date
Field being tested. The assertions now check the full-difference preview and result
counts; the fixture explicitly creates that Field. No production code changed after
this full-suite run. The warnings concern Starlette/httpx and SQLite's legacy datetime
adapter.

The subsequent affected-suite rerun passed **420 tests, zero failures/skips**, with
PostgreSQL enabled, in 84.98 seconds:

```shell
python -m pytest -q tests/test_source_format_components.py tests/test_source_format_import.py tests/test_source_format_verdicts.py tests/test_import_wizard.py tests/test_new_version_import.py tests/test_record_import.py tests/test_dataset_context.py --tb=short
```

This rerun includes all seven corrected cases, source-format validation, wizard state
and confirmation controls, both Import addresses, empty and populated tables, and the
SQLite/PostgreSQL REF-160 persistence and migration cases. All failures observed in the
full run are resolved by this rerun; the entire suite was not repeated after the test-only
corrections. Pre-commit (including unstaged new files), Ruff and `git diff --check` pass.
No CI run is claimed.

The dedicated REF-160 suite covers full classification, unchanged revisions, multiple
pages, full totals, old/new values, identifier search/status filtering, mixed apply,
deletions/Undo/publication, every mutation blocker and legacy migration behavior, stale
confirmation, exact deletion acknowledgement, rollback at audit/commit, independent
concurrent confirmations, unique-value swaps/reuse, CSV/XLSX, composite and typed
identifiers, empty files, optional-Field mapping, expiry/cancel/Concept replacement,
authorization, CSRF, cross-session tokens, duplicate form values, escaping and replay.
Real migration rollback/retry and legacy/new-Concept tests run on both engines.

Existing create-only/source-format/Date-Time/verdict-first, governance, version, audit,
CLI and migration suites are included in the full regression command. The final deletion/import path also rejects an exhausted Record revision before
incrementing it, avoiding an SQLite/PostgreSQL integer-range difference. Existing typed
parsers retain their strict inputs, including rejecting a plus sign on Integer values.
No new coercion was introduced to make test data acceptable.

## Browser and UI review

The in-app Browser backend returned `Browser is not available: iab` after the documented
skill bootstrap. Existing standalone Playwright and installed Chrome were used against
`tests/browser_new_version_app.py` on disposable loopback port 5187. No package dependency
was added. Reproduction:

```shell
.venv/bin/python tests/browser_new_version_app.py
REFRNCR_PLAYWRIGHT_MODULE=/private/tmp/ref159-browser/node_modules/playwright \
REFRNCR_BROWSER_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
REFRNCR_BROWSER_OUTPUT=/private/tmp/ref160-browser-evidence \
node tests/javascript/new_version_browser.cjs
```

**37 layout checks passed**, including a first import into an empty table,
320/768/1440 px and a 320 px JavaScript-disabled
flow. Document scroll width equals viewport width for every captured state. Tests cover
blocking upload error focus, keyboard activation, mapping, 50/7-row difference pages,
search, explicit old/new text, initially unchecked deletion acknowledgement, success
counts/filtering, disabled-action keyboard focus/visible outline/hover/description,
Deleted detail/Undo, checkbox combinations and switching to All records and back.
Screenshots were visually reviewed.
The script uses keyboard activation and native controls; it is not a screenreader-user
certification or a complete WCAG audit.

The existing `tests/javascript/dataset_versions_browser.cjs` regression also passed
after updating its import confirmation for full-file deletion acknowledgement and the
results redirect. It exercises publication/cancellation at 320/768/1440 px, Current and
Expired CSV/XLSX/JSON downloads, importing into a Concept without changing its Scheduled
snapshot, and publication without JavaScript.

Design tokens: unchanged. Shared management-table row alignment is consistent for data
cells and row headers. New reusable component: a single Import action with a linked
explanation for unavailable states, using the existing accessible rationale disclosure.
Concept editing actions sit inside the lifecycle banner; View current
version, Versions and Export sit in the Record page header. Component variants: status checkbox menu in the Record toolbar, with accessible
labels and wrapping alongside search controls; compact table action buttons. Management
table row headers share the vertical centring of data cells. Deliberate
page rules: difference/results presentation. Functional
changes are the story's new mode, eligibility, retained deletion and Undo flows. Existing
Record detail dialogs retain their JavaScript contract; the import wizard works without
JavaScript. No duplicated parser, composite Python markup or CSS `:has()` dependency was
introduced. No remaining visual inconsistency was identified in the exercised states.

Browser evidence is outside the repository at `/private/tmp/ref160-browser-evidence`;
it contains synthetic data. The app can be restarted to regenerate fresh eligible Concepts.

## Performance and remaining limits

Synthetic benchmark: 1,500 stored Records and a 22,901-byte / 1,500-row CSV, two Fields;
500 Added, 500 Changed, 500 Unchanged, 500 Deleted, zero Errors. Preview tracing includes
Python `tracemalloc`; confirmation timings do not. One local sample per engine, while
other test work could be running; no production throughput guarantee is inferred.

| Engine | Preview seconds (with tracing) | Peak traced Python MiB | Confirm seconds |
| --- | ---: | ---: | ---: |
| SQLite 3.51.0 | 1.243 | 6.18 | 4.041 |
| PostgreSQL 16.15 | 1.340 | 6.18 | 7.134 |

The original 10 MiB / 10,000-source-row / 250-column and archive/request limits remain.
Stored Records are streamed during comparison; accepted source values and identifier
sets remain proportional to the bounded source and existing Dataset. At most 50 display
differences are retained. Results filtering batches identifier queries. Ordinary Concept
sorting/search retains existing full-result memory behavior. Deleted content adds storage
proportional to retained values and Field metadata. Confirmation holds the Dataset lock;
many Deleted/Changed Records require proportional writes. No background import is added.

Undo cannot force incompatible Field definitions or occupied unique values; it fails
without altering retained data. Historic deleted Concept edits cannot be recovered beyond
the explicitly approved baseline fallback. Existing transient staging remains process-local
and TTL-bound. Real-browser checks do not imply CI end-to-end tooling or screenreader
certification. GitHub Actions must pass after an authorized future push, before merge.

## Pull request preparation

Proposed title: `feat: import complete Record versions into Concepts (REF-160)`.

What changed: add an eligible-only complete-file import mode, atomic canonical difference
application, retained Deleted Records and revision-safe Undo, with a migration and
transactional audit summaries.

Why: owners can replace the complete proposed Record version while protecting existing
Concept edits, Current and history. Legacy Concepts have an explicit safe compatibility
boundary approved by the product owner.

How tested: use the actual final verification results above. Security impact: data model
and protected browser interface extended; existing authorization policy retained. Include
migration 0018, the two compatibility decisions and restore conflict behavior for review.
Related work: [REF-160](https://cleverhorizons.atlassian.net/browse/REF-160).

No PR, commit or push was created. The working tree remains available for review.
