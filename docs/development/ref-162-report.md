# REF-162 — Dataset, Field and Settings workflow polish

Local validation complete; independent GitHub CI remains the pre-merge gate.

Review date: 2026-09-14. Branch: `feat/ref-162-ui-polish`.
Base: `6f06f298fbc6f07ef4af738c8502b82f1767fa5e` (REF-160).
Source: [REF-162](https://cleverhorizons.atlassian.net/browse/REF-162), reread including
comments and acceptance criteria; last Jira update `2026-09-14T13:31:05.854+0200`.
This report supersedes conflicting intermediate statements in the part-1 work log.
Scope is Community/core. No Cloud infrastructure, new role or runtime dependency.

## Five direct product decisions

1. **Tabbed configuration:** Dataset details, Fields, Access management and Danger
   zone, in that order. Access management is its own tab. General Fields is
   viewable but read-only; active Concept configuration retains Field editing.
2. **Inline Dataset editing:** Save and Cancel on Dataset details, replacing the
   previous right-side Dataset editor. Existing Field/destructive confirmation
   panels remain available where appropriate.
3. **Current Dataset metadata:** name, description and icon are independent of
   versions and Concepts. Existing publication snapshots retain their own metadata.
4. **Lucide names:** one input, local suggestions and manual names; unknown,
   malformed and unavailable input falls back safely without icon validation errors.
5. **Versions actions:** no page-level Review changes or Concept actions. The
   authorized Review link remains in the applicable table row.

These are the product owner's 14 September decisions and replace the corresponding
requirements in the original Codex assignment. The prior explicit instruction to
preserve current governance still applies: ADR-011 team ownership and separate
export access are authoritative. Legacy Dataset Viewer/Editor grants remain inert.
The story's generic grant terminology does not reintroduce superseded authority.

## Current metadata and immutable snapshots

`Dataset.name`, `Dataset.description` and `Dataset.icon` describe the current
Dataset. Authorized maintainers can save them with no active Concept. Metadata-only
changes do not advance the content revision, change content freshness, create a
Concept, produce a version diff, or make unchanged content publishable.

Concept creation and discard preserve current Dataset metadata. Publication still
captures name and description as snapshot provenance. Existing Current, Scheduled,
Expired and Cancelled snapshot rows are never rewritten by a metadata save. A
historical browser context combines the current Dataset identity with the selected
snapshot's Fields, identifier and Records. Stored snapshot metadata can legitimately
differ from its heading. The icon is current presentation metadata, not a snapshot
property. See [ADR-017](../architecture/adr-017-current-dataset-metadata.md).

Tests compare all version tables before/after metadata save, Concept creation and
discard with Current, Scheduled and Expired snapshots present. Existing immutable
snapshot mutation/deletion and publication concurrency tests remain in place.
Historical migration fixtures still represent their original pre-REF-162 contracts.

## Save, Cancel and concurrency

- Save validates name/description, normalizes the icon, and commits the complete
  metadata change in the existing serialized Dataset write transaction with its
  audit event. Errors remain inline with entered values escaped and retained.
- Cancel performs a read and shows the latest persisted values; it submits no write.
  Tab switches preserve the same form DOM. Cancelling the deletion panel preserves
  the inline draft and restores focus to its opener. No automatic save occurs.
- Metadata saves use existing last-committed-write-wins semantics; there is no new
  metadata compare-and-swap token or automatic merge. Two successful simultaneous
  saves yield one complete submitted tuple, never a mixture of their properties.
  A stale open form can overwrite another metadata save. This is explicit, not an
  optimistic-concurrency guarantee.
- Owning-team reassignment retains the existing expected-owner check. CSRF,
  authorization, rollback and deletion's updated-at confirmation remain unchanged.
  Metadata changes invalidate stale deletion confirmations but not content reviews.
- Publication/metadata operations serialize on the Dataset. The snapshot captures
  the metadata committed before publication; later metadata saves do not alter it.
- Browser automation waits for the intended Name focus before typing, including
  after Cancel/re-entry. This prevents the test from typing during Chrome's delayed
  fragment-focus movement. Immediate and post-tab/post-dialog value assertions remain.

## Tabs and routing

The overview's independent gear has tooltip `configuration`, an accessible name
including the Dataset name, and targets `/datasets/<key>/configure`. The Dataset
name still opens Records: Concept when there is no Current and the actor may see
that Concept; otherwise Current. The key remains visible next to the name.

General Configure reads current metadata independently. Fields reuses the existing
authorized Field selection: active Concept for maintainers, otherwise Current.
Its source is labelled and add/edit/order controls are absent even for admins.
If neither context is available, an explanation is shown while Dataset settings
remain accessible. Explicit Concept/version URLs retain their existing Field rules.

The shared tab component uses labelled tabpanels, roving tabindex, arrow/Home/End
navigation and deterministic hashes. Direct links/reload select the requested tab;
Field mutation redirects also retain `tab=fields` through fetch redirects. Without
JavaScript, authorized sections remain visible with native anchor navigation.
Access grant/revoke refreshes only its component, retains its tab, and restores
status/error focus. Permissions control visible Access/Danger tabs and remain
server-enforced. Guessing unpublished Dataset URLs does not disclose Concept Fields.

## Icons and branding

The only persisted icon values are normalized names in the bundled Lucide catalog
or null. Non-string or more-than-64-character input maps directly to null. Other
input is trimmed and lowercased and accepted only on exact catalog membership.
There is no truncation, raw-input persistence, markup parsing or path interpolation.
Unknown, malformed, unavailable, empty and oversized names all mean the default.
The input's 64-character bound is also present without JavaScript. Omitting the
icon argument on an existing service update preserves its value; submitting an
empty string clears it.

Rendering independently checks the trusted local symbol lookup and returns `table`
for anything else, including legacy invalid stored values. The SVG reference uses
one fixed local sprite URL. User input cannot select a file, template, SVG document,
HTML, external URL or request. Plain-text form redisplay remains Jinja-escaped.

The eight suggestions are house, key-round, lock, scroll-text, settings-2, table,
user and users. Manual input supports all 1,544 symbols in official `lucide-static`
0.468.0, bundled locally as a 371,613-byte sprite. SHA-256:
`b3b7a00eff7743f34c13b57528b34aa9b1505a1abf5c89545afe5559cdf1ada3`.
The upstream license matches the existing bundled ISC/MIT license. There is no
runtime CDN, download or package dependency. The input enhances to a combobox with
labelled suggestions, keyboard selection, clear, Escape and repeat-safe initialization.

The three supplied product SVGs are copied unchanged: static navigation branding,
animated login branding and favicon. Login selects the static asset for reduced
motion. Dimensions reserve space and adjacent branding makes duplicate images
purely decorative. Old block-logo files and their consumers are removed; the
`06-favicon-16px_1.svg` duplicate is excluded. See the
[asset inventory](../../src/refrncr/static/licenses/ASSET-SOURCES.md).

## Fields, keys and relationship queries

Definition is nullable plain text of at most 4,000 characters; Unit is nullable
plain text of at most 64. Empty input means null; embedded NUL is rejected. Neither
changes datatype validation, source formats, canonical Record values or arithmetic.
Both survive Concept copy, publication, Scheduled/history, review/diff and retained
Record deletion/restore. Record headers offer keyboard-accessible supplementary
information, not hover-only text.

New Dataset/Field keys share equivalent JS/Python normalization: NFKD, lowercase,
explicit whitespace/separator replacement, permitted ASCII characters and collapsed
underscores. Manual override stops following the name. Existing edit keys never
regenerate; uniqueness/reserved-key validation remains authoritative. No suffixes.

Relationship queries are bounded and authorization precedes disclosure. Measured
SELECT counts (including authentication on HTTP reads):

| Read | Empty | One relationship | Many relationships |
| --- | ---: | ---: | ---: |
| Relationship service | 2 | 2 | 2 (11) |
| Users HTTP list | 6 | — | 6 (12 memberships) |
| Teams HTTP list | 6 | — | 6 (12 memberships) |

The service uses one joined roster query and stable casefold sorting. The HTTP
fixture adds six Users and Teams with two memberships each. Tests also cover hidden
rosters and non-admin denial. XML properties record the measured counts; these are
not inferred from source or timing.

## Migrations and packaging

- `0019_ref_162` follows `0018_ref_160`: nullable Dataset icon String 32, nullable
  Field Definition String 4000 and Unit String 64 on both working and snapshot
  Field tables, with explicit length checks for Field metadata.
- `0020_ref_162` widens icon to String 64 for complete Lucide names. Downgrade to
  0019 refuses any stored name longer than 32, preventing silent truncation.
- Old rows get null metadata. No snapshot content backfill or historical retained
  payload rewrite occurs. Downgrading 0019 removes the new metadata columns and
  their values; back up before doing so.
- Legacy upgrade adapters avoid reading columns before they exist. Historical
  fixture snapshots explicitly exclude columns introduced after that fixture's
  revision, while preserving all existing assertions about values/IDs/audits.
- ORM and Alembic schemas, repeated upgrades, guarded downgrade, SQLite and
  PostgreSQL transactions, CLI preflight and installed artifacts are tested.
- Wheel/sdist packaging includes templates, controllers, sprite, product assets,
  license and migrations. The EOF hook excludes only the three normative product SVG paths so their supplied
bytes are preserved; all other hooks still apply to them. The package tests build/install into an isolated directory
  and exercise real console entrypoints and HTTP startup.

## Validation results

| Check | Final result |
| --- | --- |
| Full SQLite selection (`python -m pytest -q -k 'not postgresql'`) | 2,450 passed; 972 PostgreSQL cases deselected; 5 warnings; 522.03 s |
| Full suite with PostgreSQL 16 enabled (`python -m pytest -q`) | 3,422 passed; no skips/deselections; 7 warnings; 1,026.19 s |
| Chrome 320/768/1440 px plus no-JavaScript fallback | Passed; no page errors or external requests |
| axe-core WCAG 2 A/AA, 2.1 A/AA and 2.2 AA tags | 24 scans; 0 automatic violations; manual-review items below |
| Pre-commit, tracked files (`python -m pre_commit run --all-files`) | All 7 hooks passed |
| Pre-commit, all new files (explicit `--files`, without staging) | Applicable hooks passed; no YAML/TOML among new files |
| Ruff / diff whitespace / audit inventory | Passed; 143 operations and 65 routes |
| Query-count evidence | 2 passed, 1 PostgreSQL case skipped in the separate evidence run |

The SQLite full-run subsets include **86 migration tests**, **49 package/CLI tests**
and **42 REF-160 new-version import tests**, all passed; these counts overlap the
2,450 total and are not extra tests. The PostgreSQL-enabled full run includes
**151 migration-module cases**, **50 package/CLI cases** and **84 REF-160 import
cases**, all passed. Its 3,422 total includes SQLite cases as well; the two full-run
totals must not be added as a count of unique tests. Detailed logs/JUnit XML are in temporary
`/private/tmp/ref162-sqlite-final.{log,xml}` and
`/private/tmp/ref162-postgresql-final.{log,xml}`. Browser output is
`/private/tmp/ref162-chrome-final.log`, screenshots `/private/tmp/ref162-browser`.
Query properties are in `/private/tmp/ref162-query-counts.xml`. These diagnostic
artifacts are not staging candidates.

PostgreSQL uses a disposable `postgres:16` container on loopback port 55462, with
`REFRNCR_TEST_POSTGRES_URL` injected; the developer's existing database is untouched.
The Chrome runner uses the installed Chrome executable and an existing temporary
Playwright installation. `REFRNCR_AXE_SOURCE` points to the pinned temporary
axe-core 4.10.3 file, not a runtime application resource. The browser command is
`node tests/javascript/ui_polish_browser.cjs` with `REFRNCR_BROWSER_URL`,
`REFRNCR_BROWSER_EXECUTABLE`, `REFRNCR_PLAYWRIGHT_MODULE` and
`REFRNCR_AXE_SOURCE` pointing to that local fixture/tooling. No test dependency
was added to the application package.

The five SQLite warnings are the existing Starlette/httpx deprecation, two
JUnit-xunit2/record-property warnings, and two Python SQLite datetime-adapter
deprecations. They are recorded rather than hidden; no dependency migration was
added to REF-162. The full PostgreSQL run reports seven warnings: the same categories,
with two additional record-property warnings for the PostgreSQL discard cases.

Environment: Python 3.12.2; SQLite 3.51.0; SQLAlchemy 2.0.52; Alembic 1.19.1;
pytest 8.4.2; Ruff 0.16.5; pre-commit 4.6.2; psycopg 3.3.4;
PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2);
Chrome 152.0.7977.84; axe-core 4.10.3 installed only in a temporary test directory.

The initial diagnostic run exposed obsolete assertions and sandbox-denied local
network/process tests. Its failed result is not represented as a passing regression.
The final runs use only a disposable PostgreSQL 16 container and isolated fixtures.

## Accessibility review and limits

Chrome covers 320/768/1440 px, keyboard tab navigation, focus restoration, form-error
focus, inline draft preservation, icon suggestions/fallback, relationship panels,
Field metadata, direct routing, no horizontal page overflow and reduced motion.
The no-JavaScript fallback is tested separately; axe requires JavaScript and is not
misreported as running in that context. All `aria-controls` IDs are checked against
the actual DOM, including closed dialogs.

Axe's incomplete results are retained for manual review, not silently excluded:
hover gradients obscure automatic contrast calculation; the login password toggle
overlaps its input background; closed-dialog references need DOM verification.
Computed colors were checked against the actual surface/hover/selected palette.
The weakest reviewed small-text pairing is muted `#5d6d65` on `#e8f4ee` at 4.84:1;
links are at least 6.47:1 and the blue focus indicator at least 5.91:1 on those
surfaces. Password text is 17.92:1 on white with 120 px reserved for the toggle.
Screenshots of mobile Fields, tablet Access and desktop inline editing were inspected.
The existing Record toolbar has an `aria-label` on a generic div without a role,
reported as an incomplete `aria-prohibited-attr` check. That pre-existing issue is
outside the requested Record action changes and remains a documented follow-up.
This review is not a claim of complete WCAG certification or assistive-technology
coverage across every screen reader/browser combination.

## Scope and remaining risks

The Record-page buttons and their order are unchanged. The Record action markup
matches the branch base byte-for-byte (SHA-256
`bd691a76600f9d9a0d222b777b3d58d60d924cdeee549d9451552d07feb053ff`).
The REF-160 import flow and actions are unchanged: no changes to `record_import.py`,
its import action component or import templates/controllers. Two import tests now
create a real Field difference instead of treating a Dataset rename as publishable.
All other import/security assertions remain intact.

No design tokens or public API contracts changed. Shared components cover icons,
relationship lists, tabs and row actions; deliberate page rules cover inline Dataset
editing and general read-only Fields. Database changes are limited to the described
metadata. No new domain events, roles, grant policy or Record semantics were added.

Remaining risks: last-committed metadata saves can overwrite an older open form;
the pinned icon catalog does not include later Lucide names (safe fallback applies);
the full local sprite has the documented download size; metadata-removing downgrade
is destructive. Independent GitHub CI remains required before merge and cannot be
claimed for this unstaged, uncommitted work. No Jira/Confluence write or release was
performed. The user explicitly requested no staging, commit or push.

## Explicit staging list — proposal only

Nothing has been staged. The exact reviewed candidate paths, including deletions,
are listed below; this is not an executed staging operation.

```text
 M .pre-commit-config.yaml
 M docs/architecture/adr-014-dataset-versions.md
 M docs/architecture/adr-015-explicit-dataset-concepts.md
 M docs/development/audit-coverage.json
 M docs/development/audit-inventory.json
 M docs/development/dataset-context.md
 M docs/development/dataset-governance.md
 M docs/development/dataset-versions.md
 M docs/development/field-datatypes.md
 M docs/development/ui-foundation.md
 M src/refrncr/audit.py
 M src/refrncr/cli.py
 M src/refrncr/concept_persistence.py
 M src/refrncr/concept_upgrade.py
 M src/refrncr/dataset_diff.py
 M src/refrncr/dataset_versions.py
 M src/refrncr/diff_classification.py
 M src/refrncr/models.py
 M src/refrncr/persistence.py
 M src/refrncr/ports.py
 M src/refrncr/services.py
 M src/refrncr/static/css/components/forms.css
 M src/refrncr/static/css/components/tables.css
 M src/refrncr/static/css/layouts/app-shell.css
 M src/refrncr/static/css/layouts/auth.css
 M src/refrncr/static/css/layouts/configuration.css
 M src/refrncr/static/css/layouts/page.css
 D src/refrncr/static/images/logo-horizontal.svg
 D src/refrncr/static/images/logo-mark.svg
 M src/refrncr/static/js/refrncr.js
 M src/refrncr/static/licenses/ASSET-SOURCES.md
 M src/refrncr/templates/base.html
 M src/refrncr/templates/components/table_row.html
 M src/refrncr/templates/layouts/auth/base.html
 M src/refrncr/templates/layouts/page.html
 M src/refrncr/templates/pages/auth/login.html
 M src/refrncr/templates/pages/datasets/_record_table.html
 M src/refrncr/templates/pages/datasets/create.html
 M src/refrncr/templates/pages/datasets/delete.html
 M src/refrncr/templates/pages/datasets/detail.html
 D src/refrncr/templates/pages/datasets/edit.html
 M src/refrncr/templates/pages/datasets/fields/_form_fields.html
 M src/refrncr/templates/pages/datasets/fields/create.html
 M src/refrncr/templates/pages/datasets/fields/edit.html
 M src/refrncr/templates/pages/datasets/governance.html
 M src/refrncr/templates/pages/datasets/list.html
 M src/refrncr/templates/pages/datasets/records.html
 M src/refrncr/templates/pages/datasets/versions/list.html
 M src/refrncr/templates/pages/teams/list.html
 M src/refrncr/templates/pages/teams/readonly.html
 M src/refrncr/templates/pages/users/_user_panels.html
 M src/refrncr/templates/pages/users/list.html
 M src/refrncr/version_contracts.py
 M src/refrncr/version_persistence.py
 M src/refrncr/version_upgrade.py
 M src/refrncr/web.py
 M tests/dataset_helpers.py
 M tests/test_concept_discard.py
 M tests/test_concept_lifecycle.py
 M tests/test_concept_migration.py
 M tests/test_configure_overview.py
 M tests/test_dataset_browser.py
 M tests/test_dataset_deletion.py
 M tests/test_dataset_diff.py
 M tests/test_dataset_diff_views.py
 M tests/test_dataset_governance.py
 M tests/test_dataset_version_migration.py
 M tests/test_dataset_version_views.py
 M tests/test_dataset_versions.py
 M tests/test_field_datatype_migration.py
 M tests/test_interfaces.py
 M tests/test_migrations.py
 M tests/test_new_version_import.py
 M tests/test_packaged_cli.py
 M tests/test_table_rows.py
?? docs/architecture/adr-017-current-dataset-metadata.md
?? docs/development/ref-162-part-1-notes.md
?? docs/development/ref-162-report.md
?? src/refrncr/migrations/versions/0019_ref_162_presentation_metadata.py
?? src/refrncr/migrations/versions/0020_ref_162_icon_names.py
?? src/refrncr/presentation_metadata.py
?? src/refrncr/static/images/02-animated-grow-shrink.svg
?? src/refrncr/static/images/06-favicon-16px.svg
?? src/refrncr/static/images/07-static-logo-256px.svg
?? src/refrncr/static/images/lucide-0.468.0.svg
?? src/refrncr/static/js/icon-picker.js
?? src/refrncr/static/js/key-generation.js
?? src/refrncr/static/js/section-tabs.js
?? src/refrncr/templates/components/dataset_icon.html
?? src/refrncr/templates/components/export_access.html
?? src/refrncr/templates/components/field_metadata.html
?? src/refrncr/templates/components/relationships.html
?? src/refrncr/templates/components/section_tabs.html
?? src/refrncr/templates/pages/datasets/_edit_form.html
?? tests/browser_ui_polish_app.py
?? tests/javascript/key_generation.cjs
?? tests/javascript/ui_polish_browser.cjs
?? tests/test_key_generation_javascript.py
?? tests/test_ui_polish.py
```
