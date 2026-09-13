# REF-159 — Verdict-first import mapping

13 September 2026. Community/core. Branch `feat/ref-159-verdict-first-mapping`,
created from current `main` / `origin/main` at `7ae50f9` (merged REF-158).
The initial working tree was clean. No staging, commit, push, PR or merge is authorized.

## Scope and design input

[Jira REF-159](https://cleverhorizons.atlassian.net/browse/REF-159) is authoritative,
with REF-158, REF-154, REF-145 and REF-134 as compatibility context. The two supplied
normative artifacts were found in the sibling `docs/Documentation` directory:
`mapping-redesign-reference.html` and `mapping-redesign-spec.md`. They remain
outside the repository. Their old baseline and illustrative scaffolding do not
supersede current merged code. No `.ref-*`, sample data or inline style entered
production templates. Existing import/security, datatype, audit, architecture,
UI and REF-154/158 reports informed the implementation.

Classification: shared source-format component within the import workflow plus
mapping-specific layout. Existing design tokens, global components and component
variants are unchanged. No dependency, public API, schema, migration, canonical
format, detection outcome or persisted-value change is introduced.

## Four states and verdict rules

- `clean`: selected profile, no error, all bounded display pairs identical.
  `Read as {label} — all {total} values parsed, stored exactly as written.`
  The first view contains one verdict and Change; controls/detailed comparisons
  are inside the closed native disclosure.
- `transformed`: valid profile and at least one differing bounded display pair.
  `Read as {label} — {source} is stored as {stored}.`
  The first **differing** pair is selected, even if the first example is unchanged.
  Exactly one worked pair appears in the verdict. Additional existing comparisons
  remain available after deliberate expansion.
- `undecided`: no selected interpretation (or an unresolved proposal error).
  There is no normal verdict. An amber labelled group explicitly asks for an
  interpretation, opens by default and receives relevant error focus. It does not
  repeat a meaningless source-to-dash table. Preview cannot bypass the decision.
- `failing`: a selected format fails one or more non-empty values.
  `{failures} of {total} values do not fit {label}.`
  The error verdict and correction controls are visible initially; the action is Done.

String retains no format block. Labels come from the existing source-format
catalogue; there is no parallel format vocabulary. The exactly-as-written claim is
specifically gated by **all bounded displayed pairs**, as required by the story;
it is not a claim about retained original bytes. Complete-column validation remains
authoritative for every row, including rows beyond the three distinct examples.

`total_values` counts every non-empty value inspected by existing format validation,
including duplicates, excluding null/empty cells and formula cells as before.
Formula cells still fail authoritative import validation. `failures` is accumulated
through the complete column; neither count is the display-sample length.
Examples retain the existing 3-value / 80-character display bounds and escaped
4096-character expansion where applicable. Jinja escapes verdicts and readings;
source strings are never marked safe, logged, audited or persisted by this change.

## Candidate readings and performance

`propose_format(..., readings=...)` optionally exposes bounded matches from its
**existing** full-column detection pass. Its ordinary `(choice, error)` return is
unchanged. After the existing ambiguity/equivalence decision, two through four
matches expose the original canonical profile, its label and first canonical
result. The presentation combines that result with the first actual non-empty
source example. There is no second parser, candidate scan or changed ordering.
Remembered/confident choices return no readings. Unknown, singleton, empty and
more-than-four results use the REF-158 controls. Timestamp timezone/DST requirements
are unchanged; a local timestamp without explicit timezone has no valid instant
candidate and retains those controls.

A parser-call regression compares the exact calls with and without readings for a
200-row ambiguous column. They are identical. The current catalogue yields real
2-, 3- and 4-match cases; the defensive >4 boundary is tested by adding an existing
profile to the test catalogue view, using the real unchanged parser.

Illustrative local measurements during concurrent regression work (not a benchmark
or latency guarantee): 200 repeated ambiguous dates took 0.00422 s with readings,
0.00378 s without, and 0.00391 s for full assistance. At the 10,000-row upload limit:
0.18796 s with, 0.18502 s without, 0.19349 s for assistance. Extra output is at most
four bounded readings per undecided column. Existing detection work and temporary
canonical tuples remain proportional to rows and candidate profiles; this change
does not claim a general import performance optimization.

## Strict form envelope and state

Candidate radios use `source_N` with unchanged canonical identifiers. No same-name
source select or Date/Time component inputs are rendered in radio mode. A hidden,
checked radio with empty value preserves the **one successful source key** before
selection; choosing a real reading natively unchecks it. No real interpretation is
preselected. Timezone/fold and mapping keys remain present. Closed disclosures do
not disable successful fields. Native/offset still disable incompatible textual
components under the existing REF-158 rules.

The existing web adapter is unchanged: it rejects duplicate, missing, extra,
unknown, incompatible and contradictory component values. The 250-column tests
exercise ordinary DateTime components and ambiguous radios. Both retain the bounded
250 KiB body and 1,502 multipart parts. Canonical radio mode does not widen them.

Live `/formats` review remains read-only. Preview submissions update only secured
transient staging and invalidate dependent Preview/Confirm state as before. No new
preference, audit write or Record persistence occurs before successful Confirm.
The JavaScript Preview guard reopens/focuses an unresolved empty choice while
explicitly preserving Back and earlier-step navigation; direct and
no-JavaScript submissions still face the independent authoritative server check.

## Disclosure, accessibility and responsive behavior

Change/Done controls one disclosure, synchronizes `aria-expanded`/`aria-controls`,
and focuses the first enabled relevant input/select (or fixed-format summary).
Escape closes and returns focus to Change. Native toggle events also synchronize
labels. Reinitializing a form rebinds new fragments without duplicating listeners;
radio changes use the existing delegated refresh and generation/abort guards.
Optional dataset state and missing components are guarded. The previous active
form control is restored only when it supports focus, avoiding RadioNodeList errors.

Show all formats opens every applicable disclosure. Hide all formats closes routine
ones and **keeps undecided/failing ones open**. Individual Done/Escape remain allowed.
If only attention states exist, the global action therefore leaves them open. The
aggregate action is hidden when no applicable disclosure exists.

Without JavaScript, Change/Done is hidden and each native summary is visible and
keyboard operable. Candidate radio selection submits directly to Preview. Existing
limitations for dynamic source-type/mapping changes are explained in the no-script
message. No essential behavior relies on `:has()`; native checked state, labelled
groups, text verdicts and visible focus carry meaning. No `display: revert` is used.
Mapping example descriptions remain in the accessibility tree. A stable polite live
region announces reviewed formats/examples; errors retain their referenced IDs.

At 320 px, cards stack, verdicts wrap, and Change/Done remains visible. At 768/1440 px
existing controls align when space allows; radio choices wrap naturally. The wizard
keeps its existing page scrolling and primary actions. Screenshots and exact layout
measurements remain in `/tmp/ref159-*`, outside Git. Browser checks cover all four
states, keyboard, focus, single radio request after double initialization, fragment
rebinding, Show/Hide, blocked Preview, Back/Refresh and no-JavaScript operation.

A consistency review checked tokens, selectors, duplicate markup, hidden/submitted
state, disclosure labels, descriptions and focus. There are no new global overrides
or duplicated production composite markup. An existing UI browser test now waits
for Preview's actual loaded URL before keyboard-opening its report; this removes a
navigation race observed under concurrent test load without changing assertions,
adding sleeps or modifying product behavior.

## Impact review

Authorization, Community/Cloud boundary, CSRF, IDOR, actor/session/Dataset-bound
tokens, TTL, upload limits, full-file validation, Concept-only create-only import,
locking/revision/replay/concurrent-confirm controls and atomic audit/Records/format
preference persistence retain their existing contracts. Float ordinary/scientific,
Decimal exactness, native Excel and Timestamp/DST remain covered by regressions.
No migration, format addition/removal, public API, dependency or architectural
change. No new ADR or external architecture/process update is necessary.

## Validation

All requested local checks are green; the branch is technically ready for local review and commit. Nothing has been staged, committed or pushed.

Local tools: Python **3.12.2**, SQLite **3.51.0**, PostgreSQL **16.15**
(Debian/aarch64), Google Chrome **152.0.7977.84**, Node **24.9.0**, pytest **8.4.2**,
Ruff **0.16.5**, pre-commit **4.6.2**, Flask **3.1.3**, Werkzeug **3.1.8**,
SQLAlchemy **2.0.52**, Alembic **1.19.1**. Chrome uses temporary Playwright **1.58.2**
at `/private/tmp/ref159-browser`; no project dependency was added. The in-app
browser reported unavailable, so the repository's external real-Chrome convention
was used. PostgreSQL runs in `refrncr-ref159-pg16` on **127.0.0.1:55419**, exclusively
for disposable test data. No application data was changed.

| Check | Result | Evidence |
| --- | --- | --- |
| Full SQLite configuration | 2,268 passed; 822 existing PostgreSQL fixture skips; 0 failed; 3 existing warnings; 415.42 s | `/tmp/ref159-full-sqlite.log` |
| Full PostgreSQL-enabled configuration | 3,090 passed; 0 skipped; 0 failed; 3 existing warnings; 744.33 s | `/tmp/ref159-full-pg.log` |
| Targeted REF-159/source-format/component/wizard/import/transaction tests | 452 passed; 18 existing PostgreSQL fixture skips; 0 failed; 67.59 s | `/tmp/ref159-targeted-final.log` |
| Package/wheel, CLI/startup and JavaScript units | 56 passed; 1 existing PostgreSQL fixture skip; 0 failed; 1 existing warning; 17.05 s | `/tmp/ref159-package-javascript.log` |
| REF-159 real Chrome | 6 scenarios passed: 320/768/1440 × JS on/off; all four states, no errors/overflow | `/tmp/ref159-chrome-verdicts-final.log` |
| Existing source-format real Chrome | 6 scenarios passed: 320/768/1440 × JS on/off | `/tmp/ref159-chrome-regression-final.log` |
| Existing UI refresh real Chrome | 320/768/1440 passed; report sizes, keyboard/focus and Users regressions; no page errors | `/tmp/ref159-chrome-ui.log` |
| Existing datatype real Chrome | All nine datatypes plus canonical Record, native keyboard and no-JS flows passed | `/tmp/ref159-chrome-datatypes.log` |
| Ruff lint / formatting | Passed | `.venv/bin/python -m ruff check .` / `format --check .` |
| pre-commit | Seven configured hooks passed; new untracked files additionally checked explicitly | `--all-files` and `--files` |
| JavaScript syntax | All 26 production/test JS/CJS files passed | `node --check` for each file |
| Diff whitespace | Passed | `git diff --check` |

The full enabled suite covers the standalone configuration's existing PostgreSQL
fixture exclusions. No new skips, fixed sleeps, weakened assertions or suppressed
warnings were added. The full suites cover import service/transaction/authorization,
security, audits, formats, datatypes, package/wheel, CLI and migration compatibility.
The warnings are the existing Starlette/httpx and SQLite datetime-adapter deprecations.

Initial targeted tests exposed fixture/expected-label mistakes, corrected before
final runs. The first new browser check expected a redirect for unresolved Preview;
the existing server correctly returned mapping with HTTP 400. The final enhancement
keeps that server contract and blocks/focuses the unresolved choice before submission.
Final inspection also added a guard regression for Back from an unresolved
column: the Preview guard permits earlier-step navigation. Both JavaScript unit
harnesses passed again (2 tests, 0.23 s), and the real-Chrome acceptance was rerun.
The existing UI browser test exposed the navigation-load race described above; it
passes with an explicit load wait. Those initial runs are not claimed as successes.

Reproduction commands (repository virtual environment):

```sh
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m pre_commit run --files docs/development/ref-159-report.md tests/test_source_format_verdicts.py tests/javascript/source_verdicts_browser.cjs
git diff --check
env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short
# Set REFRNCR_TEST_POSTGRES_URL to the disposable PostgreSQL 16 database:
.venv/bin/python -m pytest -q --tb=short
# Targeted suite uses these eight files:
.venv/bin/python -m pytest tests/test_source_format_verdicts.py tests/test_source_format_components.py tests/test_source_formats.py tests/test_source_format_import.py tests/test_source_format_transactions.py tests/test_record_import.py tests/test_import_wizard.py tests/test_import_wizard_javascript.py -q --tb=short
# Package and standalone JavaScript units:
.venv/bin/python -m pytest tests/test_package.py tests/test_packaged_cli.py tests/test_cli.py tests/test_audit_time_javascript.py tests/test_import_wizard_javascript.py tests/test_login_javascript.py tests/test_navigation_javascript.py tests/test_side_panel_javascript.py -q --tb=short
# Set REFRNCR_PLAYWRIGHT_MODULE and REFRNCR_BROWSER_EXECUTABLE to the external
# installation / real Chrome. Start each matching disposable fixture first:
.venv/bin/python tests/browser_source_formats_app.py
node tests/javascript/source_verdicts_browser.cjs
node tests/javascript/source_formats_browser.cjs
.venv/bin/python tests/browser_ui_refresh_app.py
node tests/javascript/ui_refresh_browser.cjs
.venv/bin/python tests/browser_field_datatypes_app.py
node tests/javascript/field_datatypes_browser.cjs
```

Responsive evidence: at 320 px the decision cards are both **201 px** wide and
share x=61, stacking at y=480.65625 / 607.4375 in the focused JS viewport. At 768 px
they wrap naturally (321.921875 px each); at 1440 px they sit side by side. All
six scenarios assert document width ≤ viewport width. Screenshots include
`/tmp/ref159-verdict-{320,768,1440}-{js,nojs}.png` and
`/tmp/ref159-failing-{320,768,1440}.png`. Desktop and narrow failing screenshots
were visually inspected. These checks are concrete keyboard/DOM/layout evidence,
not a claim of full human screen-reader certification. Token-resolved WCAG text
contrast ratios are 9.86:1 for verdict text, 7.26:1 for the amber warning, 10.58:1
for errors, 7.30:1 for Change/Done links, 5.47:1 for examples and 9.64:1 for success.
All exceed the AA 4.5:1 threshold. The temporary browser fixtures and PostgreSQL
testcontainer were stopped after validation; the testcontainer is retained.

## Changed files and explicit staging list

The following is a **proposed** command, not executed. It is also the complete
18-file implementation/test/documentation inventory:

```sh
git add \
  docs/development/record-import-security.md \
  docs/development/ref-158-report.md \
  docs/development/ref-159-report.md \
  docs/development/ui-foundation.md \
  src/refrncr/record_import.py \
  src/refrncr/source_formats.py \
  src/refrncr/static/css/layouts/import.css \
  src/refrncr/static/js/refrncr.js \
  src/refrncr/templates/pages/datasets/import/mapping.html \
  src/refrncr/templates/pages/datasets/import/source_format.html \
  src/refrncr/templates/pages/datasets/import/wizard.html \
  tests/javascript/source_formats.cjs \
  tests/javascript/source_formats_browser.cjs \
  tests/javascript/source_verdicts_browser.cjs \
  tests/javascript/ui_refresh_browser.cjs \
  tests/test_source_format_components.py \
  tests/test_source_format_import.py \
  tests/test_source_format_verdicts.py
```

Recommended commit message:

```text
feat(import): add verdict-first mapping guidance (REF-159)
```

Recommended push command, only after local review and a user-created commit:

```sh
git push -u origin feat/ref-159-verdict-first-mapping
```

`git diff origin/main...HEAD` and its `--stat` are empty because no commits were
made. The actual changes are the unstaged `git diff` plus the three untracked
files above. Nothing is staged. No migration or format catalogue change exists.

## Remaining limits

GitHub Actions has not run for these uncommitted changes and remains the independent
pre-merge gate. The existing no-JavaScript limits for dynamic source-type/mapping
changes, staged TTL/process bounds, native Excel precision and timezone-data
behavior remain. The defensive >4 branch currently needs a catalogue test seam
because real undecided catalogue cases have at most four matches. No known remaining defect was found in the tested scope. All required local
checks passed; GitHub CI has not been run because staging/commit/push are excluded.

## Presentation review corrections

The supplied follow-up review was applied to the existing uncommitted
`feat/ref-159-verdict-first-mapping` work (the review's reference to the REF-158
branch was stale). The four findings are presentation changes in the shared import
component and mapping-specific layout; no design token, global component or
component variant changed. The existing radio envelope, parser/detection results,
state calculation, `_format_verdict`, full-column totals and no-JavaScript
summary/button enhancement chain were preserved.

1. Selected reading cards use the primary border and inset ring via
   `.source-format-controls .format-reading:has(input:checked)`. Specificity matches
   the base card rule. Focus-within remains a separate outline. Native radio state
   still communicates selection in browsers without `:has()`.
2. `source_format_label` supplies the existing descriptive Date/Time vocabulary
   to both component dropdowns and readings. DateTime readings combine their Date
   and Time component labels. Non-temporal profiles retain their catalogue label.
   Canonical IDs, raw catalogue labels and verdict labels are unchanged.
3. Limited ambiguity presents one amber explanation. Its body retains the
   `source-error-N` ID referenced by the group and radios. The redundant `.error-text`
   is omitted in this case; `data-effective-format` remains present, empty,
   focusable and polite-live. Unknown/mixed fallback keeps both the placeholder and
   full error explanation. Regression tests verify all description targets exist
   and are unique.
4. The verdict sentence uses `flex: 0 1 auto`, keeping Change immediately after it.
   At 1280 px, the measured sentence-to-button distance equals the configured
   **8 px** gap. At 390 px the button wraps below the sentence without overlap.

New assertions cover descriptive labels matching dropdowns, combined DateTime/
Timestamp labels, unchanged non-temporal labels, nonduplicated ambiguity guidance,
retained live-region/description targets and the unknown-format fallback. The old
REF-154 presentation assertion expecting “Choose source format” during limited
ambiguity was replaced with the required amber instruction and an explicit absence
check for the redundant error. This changes an obsolete presentation expectation,
not a validation assertion. No tests were skipped or relaxed to accommodate a defect.

The Chrome test now runs **320, 390, 768, 1280 and 1440 px**, with JavaScript on and
off (10 scenarios). It checks persistent selected styling after explicitly blurring
focus. For live review, an event-controlled response gate holds the existing
fragment refresh long enough to inspect the selected card, then releases it;
there is no fixed sleep or timing assumption. Existing single-request, focus,
Back/Refresh, no-JS and overflow/error checks remain. Screenshots are outside Git
at `/tmp/ref159-verdict-{width}-{js,nojs}.png`; the 1280px result was visually reviewed.

Review validation (supersedes earlier presentation results):

| Check | Result | Evidence |
| --- | --- | --- |
| Focused verdict/component/JavaScript suite | 201 passed, no failures/skips, 35.09 s | `/tmp/ref159-review-targeted.log` |
| Real Chrome 152.0.7977.84 | 10 scenarios passed, no page/console errors or horizontal overflow | `/tmp/ref159-review-chrome.log` |
| Ruff lint/format, pre-commit and whitespace | Passed before pytest; new files explicitly included | Tool output |
| Full SQLite suite | 2,272 passed; 822 existing PostgreSQL fixture skips; 0 failed; 3 existing warnings; 283.72 s | `/tmp/ref159-review-full-sqlite.log` |

The earlier PostgreSQL result above describes the initial implementation; the
presentation-only follow-up requests the full standalone SQLite configuration.
No database, migration, dependency, API, format interpretation or persistence
change was introduced. The existing staging inventory remains 18 files; no files
were staged, committed or pushed. The recommended commit remains
`feat(import): add verdict-first mapping guidance (REF-159)`. Its body should mention
that the review aligns selection styling/labels and updates obsolete ambiguity
presentation assertions. GitHub CI remains pending until a user-authorized commit
and push.


The presentation review is locally complete. All requested checks passed; the 822
skips and three deprecation warnings are the existing standalone SQLite baseline.
The temporary browser fixture was stopped. Final whitespace and pre-commit checks
passed after the report update; the working tree remains entirely unstaged.

## Native Excel date presentation review

The second review changes only presentation in `record_import.py`. Native
midnight values mapped to Date now render as dates before the existing example
comparison, so they classify as clean. DateTime examples retain their ISO time,
including midnight. Native date/datetime verdicts spell out the month, show the
stored value, and add “no day/month guessing needed” when clean. Text values and
failing/undecided verdicts retain their previous handling. Parsing, proposal,
catalogue, persistence, form contracts and state rules are unchanged.

`tests/fixtures/native_excel_dates.xlsx` is a two-column extract of the supplied
`business_objects_anwb date inconsistencies.xlsx`: all 24 original Date updated
text values and Entry date native values, with their original number formats.
Unrelated business columns were omitted. Regression tests cover Date and
DateTime targets, rendered fragments, written months, text preservation and
non-midnight rejection for Date. No schema, migration, API, authorization,
dependency or architecture changes are needed.

Additional unstaged files for this review: `tests/fixtures/native_excel_dates.xlsx`
and `tests/fixtures/README.md`. No staging, commit, push or PR was performed.

Validation after Review 2:

- Ruff check and Ruff format passed; pre-commit passed for all tracked files and
  explicitly for new unstaged files before the full suite.
- Focused verdict regressions: **33 passed** (4.58 seconds).
- Full standalone SQLite suite: **2275 passed, 822 skipped, 3 warnings**
  (316.37 seconds), with `REFRNCR_TEST_POSTGRES_URL` unset. Skips are the existing
  PostgreSQL cases; warnings are the existing dependency/SQLite deprecations.
- `git diff --check` passed. No new browser or PostgreSQL run was performed for
  this presentation correction; earlier results above remain historical.
- GitHub Actions remains the independent gate before merge and has not been run
  for these uncommitted changes.
