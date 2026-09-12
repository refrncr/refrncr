# REF-158 — Compact source-format selection

Date: 12 September 2026. Community/core scope. Local implementation on
`feat/ref-158-source-format-ux`, based on up-to-date `main` at `994eba7`.
The completion review started from clean local commits `a560a37` and `ee5debe`.
Commit, push and PR creation are authorized after successful local checks; merging
requires a separate explicit instruction and green GitHub Actions. No migration.

## Scope and decisions

Read the supplied request, repository AGENTS.md, complete Jira REF-158,
REF-154 / US-108, REF-145 and REF-134, and the existing import/security,
datatype, Concept, audit, UI-foundation and REF-154 delivery documentation.
The original implementation and this completion review both started with a clean
working tree. The two existing local commits are preserved, without rewriting history.
`git fetch --all --prune`, `git switch main`, `git pull --ff-only` and the
requested branch creation completed. Jira remains authoritative:
[REF-158](https://cleverhorizons.atlassian.net/browse/REF-158).

Classification: shared source-format component within the existing import
workflow, with workflow-specific responsive layout. Float support was explicitly
requested but already exists in the REF-154 parser; it is preserved and tested,
not reimplemented. No Cloud concerns, new dependency, architecture pattern,
public API, wizard step or separate Simple/Advanced mode were introduced.

The implementation plan covered the existing catalogue adapter, source-format
Jinja partial/controller, strict web-form validation, component/import/browser
regressions and existing documentation. Database and canonical storage remain
unchanged. No migration is necessary or added; migration 0017 stays immutable.

## Before: actual Chrome baseline

The existing Chrome regression passed at 320, 768 and 1440 px before changes.
A temporary extension of the disposable fixture measured every temporal type:

| Type | Format choices | Including empty placeholder | Initial canonical identifier |
| --- | ---: | ---: | --- |
| Date | 9 | 10 | `v1-date-ymd-dash` |
| Time | 5 | 6 | `v1-time-hms` |
| DateTime | 33 | 34 | `v1-datetime-ymd-dash-hms` |
| Timestamp | 34 | 35 | `v1-offset` |

The DateTime list included 32 textual combinations plus Native Excel. The
Timestamp list also included Z/offset. Timestamp timezone controls were present
regardless of that selection. Float `6.7e+07` already became `67000000.0` under
`v1-float`. Full-column detection and confirmed remembered proposals initialized
one canonical selection. `/formats` recomputed bounded examples on the server.
The baseline tested Advanced options, individual disclosure, keyboard/Escape,
ambiguity focus, Preview and delayed responses while typing a timezone.

Baseline screenshot: `/tmp/ref158-before.png`. Temporary baseline script:
`/tmp/ref158-baseline.cjs`. No production database was used.

## Resulting interaction and canonical contract

DateTime now offers Source type, eight Date patterns and four Time families.
The Time labels name 24/12-hour notation and minutes/seconds alongside their exact
patterns. Date and Time expose only their own components. Native Excel is one
explicit alternative (valid only for typed cells in XLSX), including Date/Time; it hides and disables textual
components. Switching back restores transient textual selections; a new native
proposal uses catalogue-order text defaults only after an explicit switch.

The unchanged canonical mapping is bidirectional:

```text
source=text + date=v1-date-dmy-slash + time=v1-time-hms
  ↔ v1-datetime-dmy-slash-hms
source=native, datatype=datetime ↔ v1-excel-datetime
source=offset, datatype=timestamp ↔ v1-offset
```

`format_components` decomposes confirmed/detected identifiers without loss.
`compose_format` accepts only an exact component set matching the original
catalogue. The HTTP adapter composes the identifier server-side. Old canonical
submissions remain compatible, but cannot be combined with component inputs.
All 57 REF-154 catalogue entries and their datatype associations are retained;
explicit catalogue-set and every-temporal-profile round-trip tests guard this.
No Cartesian option list is rendered or hidden in a control.

Timestamp Z/offset has no visible IANA/fold controls. Local text and native
Timestamp sources require explicit IANA input; DST gap/fold rules are unchanged.
Switching to Z/offset clears irrelevant zone/fold values. The server now rejects
nonempty irrelevant timezone/fold input instead of silently discarding it.

Integer/Decimal retain their five compact profiles. Float has one fixed profile
for ordinary and scientific values within the same column. `102.1059` and
`6.7e+07` show aligned canonical results `102.1059` and `67000000.0`.
Existing malformed-exponent, nonfinite, overflow/underflow and digit bounds remain.
Decimal remains exact and rejects exponents. Boolean retains all supported token
pairs in one fixed profile. Float/Boolean can disclose examples without a selector;
String has no source-format controls. Empty/null/token behavior is unchanged.

## Accessibility, refresh and responsive behavior

The same Jinja partial renders initial and refreshed Fields. Native fieldsets,
legends and visible labels group the controls. Exact selected patterns remain
available in a wrapping effective-format summary. Field errors use associated
text and `aria-invalid`; fixed-profile errors have a focusable text target.

Advanced options opens/closes every applicable Field. Change format affects its
own Field and focuses its first control (or fixed-format summary). Escape closes
that disclosure and returns focus to its summary. Generation/abort guards preserve
new timezone typing and ignore late responses. Refresh retains a still-visible
active control, so changing a Date/Time component does not move focus to Source type.
A stable polite live region announces the effective format and example updates.

The controller is idempotent for existing forms and newly imported panel content;
replaced disclosures receive one listener set. `data-source-timezone` avoids the
existing audit output-timezone attribute. No duplicated composite markup, global
style overrides, new tokens or component variants were added. A consistency review
covered selectors, hidden/disabled states, listener binding and focus.

Controls stack below 768 px and align in two columns at larger widths. Source and
Stored as retain their existing row-paired stacking. All values are escaped and
bounded to the existing three examples and parser/display length limits; no source
examples enter persistence or audit. Dynamic switching/live refresh uses JavaScript;
initial server-rendered proposals still submit through the normal form. A visible
no-JavaScript message explains that changing mappings/source types and live review
need JavaScript; detected temporal proposals can still reach Preview without it.

## Security, state and transactions

The web adapter checks allowed names, one value per expected control, datatype
membership and exact component combinations. Extra components on native/offset,
missing/duplicate components, wrong datatype IDs, unknown values and contradictory
canonical/component payloads are rejected. Nonempty irrelevant timezone/fold input
fails in both the web adapter and the import service. Hidden text selections cannot
bypass server checks.

A maximum-width regression demonstrated HTTP 413 for a valid 250-column form.
The two format/preview POST routes now allow at most 250 KiB and 1,502 multipart
parts (six per column plus CSRF/navigation), sufficient for the existing upload
column contract. This bounded request-envelope adjustment is necessary for the
extra components. The 10 MiB upload limit and all other 16 KiB form limits remain.
Live review now uses URL-encoded text controls, as normal Preview already does.
A maximum-width multipart regression exposed an intermittent missing control in
the current multipart decoder; using the text-only encoding removes that framing
problem without changing dependencies. Tests assert the browser request encoding,
all 250 columns, no writes during Preview, oversized bytes and excess multipart
parts. No broad global request-limit increase was made.

Accepted mapping/format submissions invalidate dependent Preview and Confirm
through the existing staged-state updates. Example review remains read-only.
The existing full-file validation, Concept-only writes, REF-73 authority, CSRF,
actor/session/Dataset-bound opaque tokens, TTL, upload bounds, create-only import,
Dataset locking, revision checks, replay/concurrent-confirmation guards and atomic
Unit of Work remain. Preferences are written only with a successful confirmation,
alongside Records and exactly one import-summary audit event, never per Record.

The audit inventory/matrix includes the added existing Field-list read from the
format route. No new service operation or event is registered. No schema,
canonical stored value, Field configuration, authorization, tenancy or dependency
contract changes. Architecture and deployment processes did not change; no ADR
or external Confluence edit is required.

## Completion review findings

The review compared all changes to `origin/main` (`994eba7`), reread the four Jira
stories and the repository contracts, and checked the current implementation
independently of the previous delivery claims. Scope remains Community/core.

Corrections within REF-158:

- Removed the added Simple/Advanced mode and its CSS hiding rules. The stable
  **Advanced options** action opens/closes all format disclosures and is hidden
  when none apply. Opening one **Change format** exposes its own controls and
  examples without depending on the state of other Fields.
- Removed hidden temporal source-type selectors that could hold ambiguity/error
  focus on an inaccessible CSV control. Native Excel remains explicitly selectable;
  selecting it for CSV fails the existing native-cell validation.
- Restored readable Date/Time labels in the wrapping effective summary, alongside
  exact patterns. Mobile source/stored pairs each show their own visible labels.
- Added an explanatory no-JavaScript fallback and actual no-JavaScript temporal
  Preview checks. Kept the existing single main scrolling area and ordinary footer
  actions. No shared token, global component or new component variant changed.
- Strengthened Float confirmation to import all four requested notations together
  and compare actual persisted canonical values: `102.1059`, `67000000.0`,
  `1250000.0`, `0.000602`.
- Corrected stale test navigation in the existing UI-refresh and datatype browser
  regressions (Continue to preview and explicit Concept pages). Their behavioral
  assertions remain intact; no corresponding product behavior was changed.

The distinct-example refinement is retained: show at most three distinct source
values with each value's own converted result. Full-column validation still visits
all rows, including duplicates, after the example cap is reached. Field and
remembered-format reads remain batched; composition adds no per-column query.
No new dependency, inline script/style, second parser catalogue, unbounded data
collection, source-data logging or event is introduced. The two audit-inventory
lines record the actual existing Field-list read in the review route; they do not
register an event for format choices or Preview.

## Final local validation

Only successful completed checks count below. Earlier failures exposed the above
UI/test defects or test-driver assumptions; none was bypassed with skips, fixed
sleeps, weaker assertions or suppressed warnings. Headless Chrome's native select
arrow popup did not change values even in an isolated minimal page, so the full
keyboard correction uses native type-ahead, with actual response, value and focus
assertions. The no-JavaScript check targets the rendered `noscript p` directly.

| Check | Passed | Failed | Skipped | Warnings | Duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full SQLite configuration | 2,241 | 0 | 822 | 3 | 330.39 s |
| Full PostgreSQL 16 configuration | 3,063 | 0 | 0 | 3 | 632.28 s |
| Component regression, including persisted four-value Float import | 168 | 0 | 0 | 0 | 24.57 s |
| All standalone JavaScript unit checks | 7 | 0 | 0 | 0 | 0.62 s |
| Package/wheel, CLI, migrations, transactions on both engines | 129 | 0 | 0 | 1 | 33.52 s |
| REF-158 real Chrome, JavaScript and no-JavaScript | 6 viewport scenarios | 0 | 0 | 0 | 18.02 s |
| Existing UI-refresh Chrome | 3 viewports | 0 | 0 | 0 | 8.53 s |
| Existing datatype Chrome | 9 datatypes plus Record/no-JavaScript flows | 0 | 0 | 0 | 10.28 s |
| Ruff lint | 1 | 0 | 0 | 0 | 0.03 s |
| Ruff formatting | 164 files | 0 | 0 | 0 | 0.03 s |
| pre-commit | 7 hooks | 0 | 0 | 0 | 0.73 s |
| JavaScript syntax | 25 files | 0 | 0 | 0 | 0.69 s |
| Diff whitespace | 1 | 0 | 0 | 0 | 0.02 s |

The SQLite skips are the existing PostgreSQL fixture guards; all run in the enabled
PostgreSQL configuration. The three full-suite warnings are the existing
Starlette/httpx deprecation and two SQLite datetime-adapter deprecations. The
full suites cover source formats, import/wizard, all datatype and migration
compatibility, persistence ports, audit, authorization, Concept/version, concurrency,
replay, failure/rollback and packaged CLI behavior. The final component run further
strengthens the already passing Float confirmation assertion, without a runtime change.

Commands, using the repository virtual environment:

```sh
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short
# Configure REFRNCR_TEST_POSTGRES_URL for a disposable PostgreSQL 16 database.
# Run the same full command with that environment variable set:
.venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m pytest tests/test_source_format_components.py -q --tb=short
.venv/bin/python -m pytest tests/test_audit_time_javascript.py tests/test_import_wizard_javascript.py tests/test_login_javascript.py tests/test_navigation_javascript.py tests/test_side_panel_javascript.py -q --tb=short
.venv/bin/python -m pytest tests/test_package.py tests/test_packaged_cli.py tests/test_cli.py tests/test_migrations.py tests/test_field_datatype_migration.py tests/test_source_format_transactions.py -q --tb=short
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
git diff --check
# Syntax check every file returned here with node --check:
rg --files src/refrncr/static/js tests/javascript -g '*.js' -g '*.cjs'
# Start each disposable fixture, then run its matching browser script:
.venv/bin/python tests/browser_source_formats_app.py
node tests/javascript/source_formats_browser.cjs
.venv/bin/python tests/browser_ui_refresh_app.py
node tests/javascript/ui_refresh_browser.cjs
.venv/bin/python tests/browser_field_datatypes_app.py
node tests/javascript/field_datatypes_browser.cjs
```

Browser commands use the existing external `REFRNCR_PLAYWRIGHT_MODULE` and
`REFRNCR_BROWSER_EXECUTABLE` environment convention. The in-app browser reported
`Browser is not available: iab`; real Chrome was used without adding a dependency.
Logs and screenshots remain outside Git. Review evidence uses the temporary
`ref158-review-*` logs, with full suites in `ref158-review-full-sqlite.log` and
`ref158-review-full-pg.log`, and final source-format Chrome in
`ref158-review-chrome-final.log`. The temporary PostgreSQL container is
`refrncr-ref158-pg16`, bound to loopback port 55418. No application database is used;
the suite's existing development-database connectivity check is read-only.

Versions: Python **3.12.2**, SQLite **3.51.0**, PostgreSQL **16.15** (Debian/aarch64),
Chrome **152.0.7977.84**, Node **24.9.0**, pytest **8.4.2**, Ruff **0.16.5**,
pre-commit **4.6.2**, Flask **3.1.3**, Werkzeug **3.1.8**, SQLAlchemy **2.0.52**,
Alembic **1.19.1**, setuptools **84.0.0**. No version/dependency change is included.

## Browser evidence and boundaries

REF-158 runs at **320, 768 and 1440 × 1000 px**, with and without JavaScript.
The additional UI-refresh suite uses the same widths at 900 px height; datatype
checks include 1440 and 320 px. Source-format screenshots at 320 and 1440 were
visually inspected. Exact Date/Time control measurements from the final run:

| Viewport/document width | Date x / y / width | Time x / y / width |
| ---: | --- | --- |
| 320 / 320 | 42 / 446.53125 / 236 | 42 / 526.125 / 236 |
| 768 / 768 | 64.71875 / 446.875 / 311.28125 | 392 / 446.875 / 311.28125 |
| 1440 / 1440 | 322 / 446.359375 / 510 | 848 / 446.359375 / 510 |

Assertions cover auto-detection, individual/all disclosure, accessible control
names, live summary/examples and errors, native Date/Time/DateTime switching and
restoration, both Timestamp representations, irrelevant zone clearing, mixed
Float, Preview/Back/Refresh, native keyboard selection, tab order, visible focus,
Escape restoration, repeated initialization and delayed-response protection.
No console/page errors or horizontal page overflow in the REF-158 scenarios.

No full human screen-reader session or WCAG certification is claimed. Native
select menus retain OS/browser behavior; wrapping summaries expose long patterns.
Existing Excel precision, six-fractional-digit, IANA-database and process-local
staging/TTL limits remain. No known unresolved REF-158 defect was found after the
corrections. GitHub CI is the independent gate before any merge; no merge is authorized.

## Changed files and Git administration

The complete inventory relative to `origin/main` is below. Changes to the two
older browser scripts only align their navigation with already existing behavior.

- `docs/development/audit-coverage.json`
- `docs/development/audit-inventory.json`
- `docs/development/field-datatypes.md`
- `docs/development/record-import-security.md`
- `docs/development/ref-154-report.md`
- `docs/development/ref-158-report.md`
- `docs/development/ui-foundation.md`
- `src/refrncr/record_import.py`
- `src/refrncr/source_formats.py`
- `src/refrncr/static/css/layouts/import.css`
- `src/refrncr/static/js/refrncr.js`
- `src/refrncr/templates/pages/datasets/import/mapping.html`
- `src/refrncr/templates/pages/datasets/import/source_format.html`
- `src/refrncr/templates/pages/datasets/import/wizard.html`
- `src/refrncr/web.py`
- `tests/browser_source_formats_app.py`
- `tests/javascript/field_datatypes_browser.cjs`
- `tests/javascript/source_formats.cjs`
- `tests/javascript/source_formats_browser.cjs`
- `tests/javascript/ui_refresh_browser.cjs`
- `tests/test_import_wizard.py`
- `tests/test_import_wizard_javascript.py`
- `tests/test_source_format_components.py`
- `tests/test_source_format_import.py`
- `tests/test_source_format_transactions.py`

Branch: `feat/ref-158-source-format-ux`. Preserve the existing commits and add the
review fixes with `feat(import): simplify source format selection (REF-158)`.
Push only this branch and open a focused PR to `main`, linked to REF-158. The PR
and conversation record the final commit, CI and Jira status; do not add a
self-referential commit hash to this committed report. Never merge automatically.
