# REF-158 — Compact source-format selection

Date: 12 September 2026. Community/core scope. Local implementation on
`feat/ref-158-source-format-ux`, based on up-to-date `main` at `994eba7`.
No staging, commit, push, PR, merge or migration was performed.
GitHub Actions remains the independent gate before merge.

## Scope and decisions

Read the supplied request, repository AGENTS.md, complete Jira REF-158,
REF-154 / US-108, REF-145 and REF-134, and the existing import/security,
datatype, Concept, audit, UI-foundation and REF-154 delivery documentation.
The initial tree was clean; there was no uncommitted alternative UX proposal.
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
explicit alternative, including Date/Time; it hides and disables textual
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
initial server-rendered proposals still submit through the normal form.

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

## Verification

Final successful results are recorded below. No failed/interrupted initial
run is used as passing evidence. Initial failures identified the audit-read trace,
the old fieldset assertion, minimal document-only JavaScript initialization and
the wide-form envelope and multipart decoding; each was corrected and checked
again. No skip statements, fixed sleeps, softened assertions or warning suppression
were added. The prior UI test excluding all fieldsets now
asserts the exact new source-format fieldset and legend required by this story.
The old timezone-discard test now requires rejection and no saved preference.

| Final check | Passed | Failed | Skipped | Evidence |
| --- | ---: | ---: | ---: | --- |
| Full SQLite configuration | 2,241 | 0 | 822 | `/tmp/ref158-definitive-sqlite.log`; 307.87 s |
| Full PostgreSQL 16 configuration | 3,063 | 0 | 0 | `/tmp/ref158-definitive-pg.log`; 610.33 s |
| All standalone JavaScript pytest checks | 7 | 0 | 0 | `/tmp/ref158-all-js.log`; also included in full runs |
| Targeted component/JS verification | 170 | 0 | 0 | `/tmp/ref158-url-encoded-tests.log`; 18.24 s |
| Real Chrome viewports | 3 | 0 | 0 | `/tmp/ref158-definitive-chrome.log` |
| JavaScript syntax | 25 files | 0 | 0 | `node --check` on every app JS and test CJS file |
| Ruff lint and formatting | Passed | 0 | 0 | `.venv/bin/python -m ruff check .` and `format --check .` |
| pre-commit tracked files | 7 hooks | 0 | 0 | `/tmp/ref158-definitive-hooks.log` |
| pre-commit new files | 5 hooks | 0 | 2 inapplicable hooks | `/tmp/ref158-definitive-new-hooks.log`; no new YAML/TOML |
| Diff whitespace | Passed | 0 | 0 | `git diff --check` |

The standalone SQLite skips use the existing PostgreSQL fixture guards, including
three new parameterizations of the preference-retention test. No skip logic was
introduced to make a failing test pass. The PostgreSQL configuration executes these
cases. Both full configurations include source formats, Record imports, wizard,
datatypes, existing migrations, persistence ports, audit, authorization, Concepts
and version regressions. Three existing warnings remain: Starlette/httpx and two
SQLite datetime-adapter deprecations. No warning is suppressed.

Commands use the repository virtual environment:

```sh
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short
# Disposable PostgreSQL 16 database on 127.0.0.1:55418; test-only credentials.
REFRNCR_TEST_POSTGRES_URL='postgresql+psycopg://ref158:ref158-test-only@127.0.0.1:55418/ref158' \
  .venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m pytest tests/test_source_format_components.py \
  tests/test_import_wizard_javascript.py -q --tb=short
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
git diff --check
.venv/bin/python tests/browser_source_formats_app.py
REFRNCR_PLAYWRIGHT_MODULE=/tmp/ref134-browser/node_modules/playwright \
REFRNCR_BROWSER_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  node tests/javascript/source_formats_browser.cjs
```

Runtime versions: **Python 3.12.2**, **SQLite 3.51.0**, **PostgreSQL 16.15**
(Debian 16.15-1.pgdg13+2, aarch64). PostgreSQL tests use only the disposable
`refrncr-ref158-pg16` container on loopback port 55418. The existing development
database is untouched apart from the suite's pre-existing read-only connectivity
check. The disposable browser fixture and PostgreSQL container were stopped
after verification.

Browser: **Google Chrome 152.0.7977.84**. The in-app browser bootstrap reported
`Browser is not available: iab`; the existing external Playwright/real Chrome
fixture was used. No browser package was added to the project. Viewports:
**320, 768 and 1440 × 1000 px**. No page errors or console errors were observed.
Screenshots `/tmp/ref158-{320,768,1440}.png`; 320 and 1440 visually inspected.
Numeric screenshots: `/tmp/ref158-numeric-{320,768,1440}.png`.

Exact Date/Time layout measurements from Chrome, in CSS pixels:

| Viewport | Document width | Date x / y / width | Time x / y / width |
| ---: | ---: | --- | --- |
| 320 | 320 | 42 / 446.59375 / 236 | 42 / 526.1875 / 236 |
| 768 | 768 | 64.71875 / 636.75 / 311.28125 | 392 / 636.75 / 311.28125 |
| 1440 | 1440 | 322 / 555.234375 / 510 | 848 / 555.234375 / 510 |

Chrome covers detected DateTime, all-fields/individual disclosure, Date/Time
changes and their summaries/examples/errors, focus, keyboard Tab/Escape,
Preview/back preservation, native/text restoration, real native Excel Date/Time/
DateTime, ordinary/scientific Float in one column, ambiguity focus, Z/offset versus
local Timestamp, timezone clearing, delayed-response protection, safe repeated
initialization and page-overflow assertions at each width.

## Remaining boundaries

No full human screen-reader session or WCAG certification is claimed. Native
select option menus retain browser/OS presentation; the wrapping summary exposes
long selected patterns. Existing native Excel precision, six fractional digits,
IANA database and process-local staged-upload TTL limitations remain.
GitHub CI cannot run for these uncommitted/unpushed changes and is required before
merge. This delivery is for local review and commit only.

## Files and recommended Git actions

The explicit list below is also the changed-file inventory. Catalogue/service/web
files implement composition and validation; templates/CSS/JS implement the shared
source-format UI; tests/fixtures establish behavior; documentation records the
contract and its audit-read trace. All changes remain unstaged.

Recommended commands only — none executed:

```sh
git add \
  docs/development/audit-coverage.json \
  docs/development/audit-inventory.json \
  docs/development/field-datatypes.md \
  docs/development/record-import-security.md \
  docs/development/ref-154-report.md \
  docs/development/ref-158-report.md \
  docs/development/ui-foundation.md \
  src/refrncr/record_import.py \
  src/refrncr/source_formats.py \
  src/refrncr/static/css/layouts/import.css \
  src/refrncr/static/js/refrncr.js \
  src/refrncr/templates/pages/datasets/import/mapping.html \
  src/refrncr/templates/pages/datasets/import/source_format.html \
  src/refrncr/web.py \
  tests/browser_source_formats_app.py \
  tests/javascript/source_formats.cjs \
  tests/javascript/source_formats_browser.cjs \
  tests/test_import_wizard.py \
  tests/test_import_wizard_javascript.py \
  tests/test_source_format_components.py \
  tests/test_source_format_import.py \
  tests/test_source_format_transactions.py
git commit -m "feat(import): simplify source format selection (REF-158)"
git push -u origin feat/ref-158-source-format-ux
```

Final verified Git state: **19 modified tracked files, 3 new untracked files,
0 staged files**, on `feat/ref-158-source-format-ux`. No unrelated files or known
secrets were added. The explicit `git add` list above contains all 22 paths.
