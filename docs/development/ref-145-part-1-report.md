# REF-145 part 1 — work report

Status: part 1 implemented and locally validated; stopped without commit or push.
This is historical evidence, not a final correctness claim. The independent
[part-2 report](ref-145-part-2-report.md) supersedes its completeness assessment
and records corrections to embedded Record forms, typed formatter bounds and
export cleanup, plus PostgreSQL 16 and actual browser validation.

## Starting Git state

Started on clean `main`; `git pull --ff-only` reported already up to date.
Created `feat/ref-145-canonical-field-datatypes`. No commit or push authorized.
Starting/current HEAD: `3be23654be3c2999d06db3d3817d570d838d1dab`.
Final working tree: 20 modified tracked files and 8 new files, all listed below;
no staged changes. The changes are ready for review, not committed.

## Existing contracts (recorded before production changes)

- Current Jira REF-145 (updated 2026-09-10) is the functional source. REF-150,
  REF-151, REF-152, REF-153 and REF-154 define excluded follow-up work.
- Community/core scope confirmed against AGENTS.md, ADR-009/010/011/012 and
  Confluence Architecture Overview & Domain Model and Security Architecture.
- Stable field IDs in services, ORM, migration 0006, forms and fixtures are
  `string`, `number`, `boolean`, `date`. `number` means exact Decimal, not Float.
- String strips Unicode whitespace, normalizes NFC, maps empty to null, rejects
  internal Unicode Cc controls and limits normalized UTF-8 to 1000 bytes.
  Its key is `s` followed by UTF-8. No semantic business validation exists.
- Decimal accepts ungrouped ASCII `-?(0|[1-9][0-9]*)(\.[0-9]+)?`, with 20
  integer digits, 18 fractional digits and 38 total digits. Plus, leading
  zeroes and exponent notation are rejected. Trailing fractional zeroes and
  signed zero normalize in the `n`-prefixed exact ASCII key.
- Boolean accepts only `true`/`false`, with historical binary keys `b1`/`b0`.
  Date requires complete ASCII Gregorian YYYY-MM-DD, years 0001–9999, key `d`.
  Empty text is null for each type. Null has no RecordValue row.
- `DatasetRecordService._validated_values` is shared by manual create/edit and
  confirmed/previewed CSV/XLSX import. It checks required fields and identifiers,
  creates canonical uniqueness keys and bounds the length-prefixed composite
  identifier to 2000 bytes. Record updates compare canonical keys for no-ops
  after checking expected revision. There is no business Record API yet.
- Import profiling calls the same canonicalizer for the four existing types.
  Existing XLSX conversion maps native booleans and dates to their old textual
  contracts; source-format recognition and temporal source reinterpretation
  belong to REF-154. Preview fingerprints schema and confirmation revalidates
  under the Dataset lock. Bulk import emits one structural summary event.
- Persistence stores one of string_value, number_value Numeric(38,18),
  boolean_value, date_value. Reads reconstruct Decimal from canonical_value_key
  because SQLite Numeric is not an exact decimal representation. Uniqueness
  uses binary canonical keys; identifier components are ordered by field key.
- Listing sorts typed Python values by the identifier, with UTF-8 byte ordering
  for String, missing components last and Record ID tie-breaking. Export uses
  the same key through bounded external sorting. No Record value filtering
  interface or query port currently exists; do not invent one in this story.
- Field create, direct update and combined identifier update are separate
  existing service entrypoints. Required additions and identifier membership
  changes are blocked after Records exist. Datatype changes currently use the
  same broad Record-count guard, even for an all-null Field. The port already
  provides `field_has_values`, so no new persistence port is needed.
- Field definitions have no expected-revision contract. Record edits/deletes,
  field ordering and Dataset ownership have their own existing stale checks.
  Do not introduce a general schema revision as incidental datatype work.
- Maintenance follows current owning-team membership or active Community admin,
  checked inside the serialized write UoW. Browser actors come from sessions;
  CSRF and non-disclosing Dataset/Record lookup rules remain in force.
- Field and manual Record mutations audit stable object/action/actor identifiers
  without Record values or free-text context. Mutation, timestamps and audit
  share one transaction. Export alone retains ADR-012's separate prepared-request
  audit transaction. No event-contract extension is necessary for datatypes.
- Historical migrations 0001–0013 are immutable. The current environment owns
  transactional SQLite DDL with FK suspension and post-migration verification;
  PostgreSQL uses transactional DDL. Historical tests construct old schemas
  separately where production populated downgrades are intentionally blocked.
- CSV/JSON Decimal serialization avoids Float. XLSX retains exact high-precision
  Decimal as text above its 15-digit safe numeric-cell limit. Existing String
  spreadsheet formula protection must remain intact.

## Implementation plan

1. Add one small standard-library datatype catalog, retaining old IDs/keys and
   parser semantics; derive service type choices from it.
2. Extend Field/Record validation and identifier guards, persistence decoding,
   typed comparison and canonical web/export presentation through this catalog.
3. Expand only the Field type CHECK constraint with a new preflighted migration;
   reuse existing text storage for new canonical types, avoiding old value rewrites.
4. Extend the shared Field form for nine labels and Float identifier eligibility.
   This is explicitly requested product functionality; no design tokens or output
   controls are needed.
5. Add datatype, service, dual-database, migration and UI regressions, run required
   checks, review the diff and complete this report with exact evidence.

## Implemented result

The single framework-independent catalog is `src/refrncr/datatypes.py`. It
defines all nine IDs/labels, parsers, canonical formatters, stable key prefixes,
Python comparison types, null behavior, identifier eligibility and fixed error
classes. `services.canonicalize_record_value` remains the compatible application
entrypoint and translates domain errors without embedding submitted values.

| Type | Implementation and canonicalization |
| --- | --- |
| String | Existing Unicode whitespace stripping, NFC, 1000 UTF-8-byte and Cc-control rules; no semantic validation |
| Integer | Exact Python int, 1000-digit limit, leading zero normalization, no plus/grouping/exponent |
| Decimal | Historical `number` ID and exact `n` key; 20 integer + 18 fractional digits, no exponent/rounding/Float parsing |
| Float | Finite binary64, deterministic shortest round-trip `repr`; signed zero becomes `0.0`; nonzero-to-zero underflow rejected, subnormals retained |
| Boolean | Exactly lowercase `true`/`false`; existing `b1`/`b0` keys unchanged |
| Date | Strict Gregorian YYYY-MM-DD, years 0001–9999 and leap-year validation |
| Time | Clock time without zone, mandatory seconds and 0–6 fractional digits |
| DateTime | Civil date/time with `T`, without offset/timezone |
| Timestamp | Explicit RFC 3339 instant normalized to UTC `Z`, preserving up to six fractional digits |

Every parser bounds raw input to 4096 characters. Integer, Decimal, Float and
temporal limits are separately enforced. Non-String input rejects whitespace,
controls and trailing content. Fractional zero suffixes are removed; nonzero
fractions are not rounded. Timestamp offset syntax is bounded to 23:59 and UTC
overflow is rejected. RFC 3339 `-00:00` retains the known UTC instant and
normalizes to `Z`. Unknown type IDs fail even for null values.

Field creation, ordinary editing and combined identifier editing share the
catalog. All-null Fields may change type even when other Records exist; populated
Fields reject type changes under the Dataset lock. Same definitions are no-ops.
Float is rejected in all identifier mutation entrypoints and Record validation.
Existing Field metadata has no general revision counter; Record/order/ownership
stale contracts remain intact rather than inventing a new schema revision.

Record create/edit, import confirmation and import profiling share canonical
parsing. Canonical-equivalent updates retain revision, timestamps and audit.
The persistence adapter decodes every value from its validated key and checks
column shape/consistency. New types reuse canonical text storage, while the
original four retain their columns and IDs. Exact Decimal reads still use the
key instead of SQLite's approximate Numeric projection.

Canonical binary uniqueness/identifier keys remain the equality contract.
Ordering uses catalog-provided typed Python values, with null/incomplete values
last, UTF-8 String order and existing identifier/Record-ID tie-breaking. Existing
Record listing/export are covered on both engines. No Record filter interface
exists, so none is added; the catalog's typed values/order are reusable by one.

CSV/JSON handle the new types canonically, including native JSON numbers and UTC
Timestamp strings. XLSX uses canonical text for new types to preserve digits,
fractions and explicit UTC. Existing four-type export and formula protections
remain. No configurable output format or source recognition is implemented.

## Migration and compatibility

Added `0014_ref_145_field_datatypes.py` after `0013_ref_39`. It validates old
type IDs, canonical keys, finite numeric projections, typed storage shape,
unique-key consistency and identifier definitions before changing only the
Field CHECK constraint. It does not rewrite values, identifiers or audits.
Unknown/malformed history and new-type downgrades fail before DDL. Real tests
inject a DDL failure and prove rollback of constraint, contents and revision on
both databases. Binary keys are type/length checked before conversion.

Historical migrations 0001–0013 are unchanged. The historical-fixture helper
first performs the supported old-type downgrade from 0014 to 0013. Existing
migration tests then continue constructing their original older schemas; the
REF-73 populated downgrade guard is not weakened. The CLI's existing REF-121
schema check recognizes the new head. No backfill or new columns are needed.

Old tests which intentionally rejected Integer/DateTime now reject genuinely
unknown types; a populated-type-change assertion uses an actually populated
Field. Presentation assertions use catalog labels. These are requested contract
updates, with retained error/no-mutation assertions and new positive coverage;
no assertions were dropped and no skips were added.

## Authorization, audit and impact review

- Authorization/security: existing Dataset capability checks, trusted session
  actor, CSRF, scoped/non-disclosing lookup and UoW locking remain unchanged.
  Float rules are enforced server-side as well as in the UI.
- Audit: existing minimal structural events and summary import context remain;
  no Record values or new free-text metadata. Real database audit-insert failures
  roll back Field/Record state and timestamps. Validation, denial, stale, no-op,
  rollback and import replay regressions remain covered.
- Schema/migrations: one new type-constraint migration, no value backfill, no
  tenant/organisation columns. SQLite and PostgreSQL share canonical semantics.
- APIs/dependencies: no new route/API, framework, package or build process. The
  existing canonicalization service entrypoint and ports remain reusable.
- Architecture/docs: ADR-013 and the datatype guide record catalog/text-storage
  decisions, precise limits, upgrade/downgrade and the future-datatype procedure.
  Confluence [Architecture Overview & Domain Model](https://cleverhorizons.atlassian.net/wiki/spaces/RE/pages/41877505)
  was updated and reread at version 11 with an explicit local/uncommitted REF-145
  section. Earlier sections were retained; Jira scope/status was not modified.
- Community/Cloud: entirely Community/core, without hosted-service composition,
  tenant isolation, billing or other Cloud infrastructure.

## UI classification and consistency review

This is the explicitly requested product functionality in the shared Field form.
The form and configuration table use catalog labels. Server-rendered and
dynamically loaded forms derive identifier eligibility from the catalog; the
existing controller disables/clears Float membership and re-enables eligible
types without silently restoring a selection. Record forms use canonical text,
and the existing numeric table variant also applies to Integer and Float.

- Changed design tokens: none.
- Changed shared components: Field-form macro and existing panel controller;
  canonical Record prepopulation/presentation.
- Changed variants: existing numeric-column presentation includes Integer/Float.
- New page-specific styling: none.
- Functional changes: nine-type selection and the story's canonical/identifier/
  empty-Field mutation rules.
- Consistency review: no new CSS values, stylesheets, duplicated composite
  markup, framework or controller. Labels/eligibility are catalog-derived.
  Existing focus, keyboard, no-JavaScript fallback and escaping tests pass.
  No new browser screenshots or full assistive-technology certification is claimed.

## Changed files

Production:

```text
src/refrncr/datatypes.py
src/refrncr/services.py
src/refrncr/ports.py
src/refrncr/models.py
src/refrncr/persistence.py
src/refrncr/record_import.py
src/refrncr/dataset_export.py
src/refrncr/web.py
src/refrncr/cli.py
src/refrncr/migrations/versions/0014_ref_145_field_datatypes.py
src/refrncr/static/js/refrncr.js
src/refrncr/templates/pages/datasets/fields/_form_fields.html
src/refrncr/templates/pages/datasets/detail.html
src/refrncr/templates/pages/datasets/_record_table.html
```

Tests:

```text
tests/test_field_datatypes.py
tests/test_field_datatype_services.py
tests/test_field_datatype_migration.py
tests/test_dataset_fields.py
tests/test_dataset_records.py
tests/test_dataset_browser.py
tests/test_migrations.py
tests/dataset_helpers.py
tests/javascript/side_panels.cjs
```

Documentation:

```text
docs/architecture/adr-013-canonical-field-datatypes.md
docs/development/field-datatypes.md
docs/development/record-import-security.md
docs/development/dataset-export-security.md
docs/development/ref-145-part-1-report.md
```

## Tests and exact commands

Python 3.12.2 and SQLite 3.51.0 from the repository `.venv`; the local test server
reports **PostgreSQL 15.18 (Homebrew)**. The dual-engine
fixtures use the existing disposable local PostgreSQL database and isolated
per-test schemas. No production database was accessed.

```sh
export REFRNCR_TEST_POSTGRES_URL=postgresql+psycopg://refrncr:refrncr@localhost:5432/refrncr_test
.venv/bin/python -m pytest tests/test_field_datatypes.py -q --tb=short
.venv/bin/python -m pytest tests/test_dataset_fields.py tests/test_dataset_records.py tests/test_dataset_business_keys.py tests/test_dataset_export.py tests/test_record_import.py tests/test_migrations.py -q --tb=short
.venv/bin/python -m pytest tests/test_field_datatypes.py tests/test_field_datatype_services.py tests/test_field_datatype_migration.py tests/test_dataset_browser.py -q --tb=short
.venv/bin/python -m pytest tests/test_side_panel_javascript.py -q --tb=short
.venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m pre_commit run --files src/refrncr/datatypes.py src/refrncr/migrations/versions/0014_ref_145_field_datatypes.py tests/test_field_datatypes.py tests/test_field_datatype_services.py tests/test_field_datatype_migration.py docs/development/field-datatypes.md docs/architecture/adr-013-canonical-field-datatypes.md docs/development/ref-145-part-1-report.md
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
PYTHONPYCACHEPREFIX=/tmp/ref-145-pycache .venv/bin/python -m compileall -q src tests
node --check src/refrncr/static/js/refrncr.js
node --check tests/javascript/side_panels.cjs
git diff --check
```

Completed results:

| Check | Result |
| --- | --- |
| Focused catalog tests | 193 passed |
| Original Field/Record/identifier/import/export/migration regressions, both engines | 218 passed, 2 existing deprecation warnings |
| Catalog/service/migration/web focused run, both engines | 411 passed |
| Extended panel-controller test | 1 passed |
| First full pytest run, both engines | 2018 passed, zero skips, 3 existing deprecation warnings |
| Final full pytest run after review corrections, both engines | **2018 passed, zero skips**, 3 existing deprecation warnings; 214.70 seconds |
| Pre-commit all tracked files | Passed |
| Pre-commit explicit new files | Applicable hooks passed; YAML/TOML have no matching files |
| Ruff check | Passed |
| Ruff format-check | Passed: 119 Python files |
| Python compilation, both Node syntax checks, diff whitespace check | Passed |

The first sandboxed PostgreSQL attempt was blocked by localhost network policy;
the permitted rerun above passed. Early red tests identified superseded datatype
and empty-Field assertions plus historical fixture/head assumptions; these were
updated to REF-145 and verified by the later green runs. Final review also checked
RFC 3339 `-00:00`, canonical Float JSON zero and bounded historical binary-key
conversion. The final full run above covers these final edits. The warnings are
the existing Starlette/httpx deprecation and two SQLite historical-fixture
datetime-adapter deprecations; no dependency changes were made.

## Remaining limits and stop conditions

No open implementation blocker is known. Native SQL filtering/indexed new-type
ordering, a general Field revision, source formats, output configuration and
JSON import are not present or invented. Six fractional digits and the documented
technical numeric/String bounds are deliberate limits. XLSX's canonical-text
fallback for new types preserves their values until REF-152 owns typed formatting.
Local PostgreSQL evidence is for 15.18; the configured GitHub Actions PostgreSQL
16 gate still requires a future push/CI run, which is not authorized in this part.

No REF-150, REF-151, **REF-152, REF-153 or REF-154** functionality has been
implemented. No commit, push, PR, merge or release was made. GitHub Actions has
not run for this uncommitted change and remains the independent gate before
merge. Stop after finalizing this report and local checks, as requested.
