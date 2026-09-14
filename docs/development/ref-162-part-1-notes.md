> Historical work log: intermediate decisions below are superseded by the
> [REF-162 delivery report](ref-162-report.md) and the Jira product decisions of 2026-09-14.

# REF-162 implementation notes (part 1)

Work starts from main `6f06f29`, on `feat/ref-162-ui-polish`. No staging,
commits or pushes. Full regression and final delivery review belong to part 2.

## Plan and boundaries

- Reuse Dataset governance services and extract the export-access composition for
  Configure Dataset. The product owner confirmed that current team ownership and
  independent export access supersede the story's obsolete Viewer/Editor wording.
- Adjust Configure/Versions page actions and Dataset list navigation/presentation.
  Preserve Record action order and all REF-160 import actions.
- Add one shared generated-key controller and equivalent Python canonicalization;
  retain existing validation and immutable keys on edit.
- Batch relationship reads through membership ports, keeping administrator-only
  membership visibility and the existing responsive table/card presentation.
- Reuse pinned Lucide 0.468.0 macros for a curated, accessible Dataset icon picker.
  Icons are current Dataset presentation metadata, not published Record content.
- Carry bounded plain-text Field Definition and Unit through working/snapshot
  models, ports, copy, review, publication and historical upgrade helpers.
- Add migration `0019_ref_162` after the existing `0018_ref_160` head. New optional
  columns have null defaults; existing content and historical metadata stay intact.
- Replace branding with the three supplied SVGs, preserving source bytes and
  selecting the static logo for reduced motion.
- Add focused Python/JavaScript and browser checks, including query counts,
  authorization, migrations, lifecycle, escaping and narrow-screen presentation.

No public API endpoint, dependency, authorization policy, tenant model, Record
canonicalization, source-format contract or version selection algorithm changes.
The requested Dataset-list exception selects an authorized Concept only when no
Current exists. Document changes in the existing UI/model/upgrade/asset guides.

## Preparation

All three supplied SVG sources were read in full. The animated source uses CSS
radius animation on concentric rings; static and favicon geometry differ and must
remain separate. The duplicate favicon is excluded.

The in-app browser bootstrap returned `Browser is not available: iab`; browser
checks used the repository's existing standalone Playwright/Chrome approach.

## Implementation decisions

This story is Community/core work. The existing application services, SQLAlchemy
ports, Jinja macros and fragment controllers are extended; no framework or
runtime dependency was added. The user's governance clarification is binding:
existing team ownership and separate export access remain authoritative. There
is no new Viewer/Editor grant implementation.

The icon is current Dataset presentation metadata, shared by Dataset contexts.
Changing only the icon is audited without advancing the Concept content revision.
Definition and Unit are versioned Field metadata, carried through creation, edit,
Concept copy, publication, Scheduled/Current/history, review and retained deletion.
They do not change Record values or import/export source formats. Empty strings
clear them to null; omitted service update arguments preserve existing values.

The shared key controller stops following the name after manual key input and
handles input/change, focus/autofill, pageshow and fragment initialization. A
redisplayed nonempty key remains authoritative. Python and JavaScript share tested
canonicalization vectors. Existing key validation and uniqueness remain final.

Relationship lists use one joined persistence projection. The service checks
Community administrator authority; ordinary users do not receive the roster.
Configure reuses one extracted export-access macro and the existing services,
with distinct inline IDs and component-only refresh. Existing standalone panels
retain their presentation. Record action order and REF-160 import actions are
unchanged.

## Migration and bounds

The inspected previous Alembic head was `0018_ref_160`; the new head is
`0019_ref_162`. It adds:

| Table | Nullable columns | Bounds |
| --- | --- | --- |
| datasets | icon | String 32; server allowlist |
| dataset_fields | definition, unit | 4,000 / 64 characters; database length checks |
| dataset_version_fields | definition, unit | 4,000 / 64 characters; database length checks |

Existing rows receive null metadata. No historical content is inferred or rewritten.
Legacy retained-deletion JSON uses null defaults. Historical upgrade adapters read
pre-metadata schemas explicitly; reflection during snapshot append is per snapshot,
not per Field. CLI schema validation and publication/concept preflight recognize
the new head. Historical test fixtures are adjusted to construct their intended
old schemas without weakening production downgrade safeguards.

SQLite and PostgreSQL upgrade, preservation and repeat-upgrade cases passed.
Downgrade removes the new metadata and is therefore lossy for those properties;
restore a backup when their preservation is required.

Definition and Unit are escaped plain text and reject embedded NUL. Arbitrary
HTML/SVG, URLs and unknown icon identifiers fail server validation. Lucide 0.468.0
is already bundled locally, with ISC/MIT notices in `LICENSE-Lucide.txt`. The
allowlist is **house, key-round, lock, scroll-text, settings-2, table, user, users**.
There is no runtime CDN or dynamically constructed template path. Empty icon
selection uses Table. Dataset icons are decorative alongside visible names.

## Focused verification

These runs overlap; the counts must not be added as unique coverage. This is
part-1 evidence, not a full regression or final Definition of Done.

- Latest combined UI, interface, metadata, migration, JavaScript wrapper and audit
  run: **321 passed**, no skips, using SQLite and disposable PostgreSQL 16.
  Files: test_ui_polish, test_interfaces, test_dataset_version_migration,
  test_concept_migration, test_migrations, test_key_generation_javascript and
  test_audit_catalog. One existing Starlette/httpx deprecation warning was emitted.
- Affected Dataset browser, Configure, governance, versions/diff/lifecycle,
  users/teams/memberships and existing side-panel/navigation/login JavaScript
  wrappers: 529 passed in the initial 530-case run; its sole stale key-help-text
  assertion was updated and passed in the targeted rerun. No known failing case
  remains from that run.
- The migration/CLI/persistence/interface run initially had 169 passes and three
  stale interface expectations. All three are corrected and covered by the
  successful 321-case run. CLI and persistence checks passed.
- New tests cover key parity and manual overrides; metadata bounds, escaping,
  atomic rejection and full snapshot lifecycle; icon validation/clearing;
  authorization and CSRF denial; inline export grant/revoke; Current versus
  Concept routing; query counts; migration preservation and retained deletion.
- Discard/REF-155 and the new metadata-restoration/icon-preservation test:
  **110 passed** on SQLite and PostgreSQL.
- Real Chrome **152.0.7977.84** passed at **320, 768 and 1440 pixels**, including
  actual badge/cell vertical centers (within 2 pixels), no page overflow or
  duplicate IDs, side-panel focus return, icon keyboard selection/clearing,
  repeated picker initialization, empty results/Escape, Field creation, inline
  access success/error/revoke and relationship navigation. Reduced-motion source
  switching and the no-JavaScript form/select fallback passed. No page errors or
  external requests were observed.
- Ruff lint and formatting checks passed on the changed Python files/application
  source (70 files checked for formatting). `git diff --check` passed.
- The three copied logos match the supplied files byte-for-byte. No old block-logo
  consumer or duplicate favicon remains in application/test sources.

Browser reproduction uses the existing optional standalone Playwright installation:
start `python tests/browser_ui_polish_app.py`, then run
`node tests/javascript/ui_polish_browser.cjs` with `REFRNCR_PLAYWRIGHT_MODULE` and
`REFRNCR_BROWSER_EXECUTABLE` pointing to local Playwright/Chrome. The fixture is
loopback-only with disposable in-memory data. Temporary screenshots/logs are in
`/private/tmp/ref162-browser/` and `/private/tmp/ref162-*.log` for this session.
The in-app browser was unavailable; no dependency was added to compensate.

## Query counts

| Path | Relationships compared | SELECT count |
| --- | --- | --- |
| MembershipService.list_relationships | 0 / 1 / 11 | 2 / 2 / 2 |
| GET /users (administrator) | 0 / 12 | 6 / 6 |
| GET /teams (administrator) | 0 / 12 | 6 / 6 |

The service count includes actor authorization plus one joined roster read.
HTTP counts include existing session/actor and page reads. Both engines exercise
the service count; the HTTP fixture exercises SQLite. Counts do not grow with
Users, Teams or memberships. Non-admin Teams remains limited to the actor's
visible teams and does not disclose other relationships.

## UI consistency and impact

- Tokens: none added or changed; existing spacing, colour and typography reused.
- Shared components: key generation, icon picker, Field metadata, relationship
  list, export access, Dataset context icon and product branding.
- Variants: relationship table/card layout, Dataset badge alignment and inline
  export access with scoped IDs and appropriate heading levels.
- Deliberate page rules: Configure link/access placement, Versions page action
  removal, and no-Current list routing.
- Functional changes: the explicitly requested generated keys, optional icon and
  versioned descriptive Field metadata, membership projections and direct routing.
- Impact review: schema migration and internal port arguments change; public API
  endpoints, authorization policy, tenant isolation, Record canonicalization,
  import/export formats and dependency set do not. Existing escaping, CSRF,
  non-disclosing denial, transaction and audit behavior are preserved.

## Open for part 2

Run the full configured pytest, pre-commit and Ruff checks, perform the definitive
scope/security/accessibility/diff review and prepare the final report. GitHub CI
remains the independent gate before merge. No full regression, full pre-commit run,
CI claim, staging, commit, push or merge was performed in part 1. No known unresolved
functional defect remains from the focused checks; broader regression and the
final consistency review are deliberately still open.

## Changed files

The following is the complete part-1 working-tree inventory (`M` modified,
`A` new/untracked, `D` removed); nothing is staged.

```text
M docs/development/audit-coverage.json
M docs/development/audit-inventory.json
M docs/development/dataset-context.md
M docs/development/dataset-governance.md
M docs/development/dataset-versions.md
M docs/development/field-datatypes.md
A docs/development/ref-162-part-1-notes.md
M docs/development/ui-foundation.md
M src/refrncr/audit.py
M src/refrncr/cli.py
M src/refrncr/concept_persistence.py
M src/refrncr/concept_upgrade.py
M src/refrncr/dataset_diff.py
M src/refrncr/dataset_versions.py
M src/refrncr/diff_classification.py
A src/refrncr/migrations/versions/0019_ref_162_presentation_metadata.py
M src/refrncr/models.py
M src/refrncr/persistence.py
M src/refrncr/ports.py
A src/refrncr/presentation_metadata.py
M src/refrncr/services.py
M src/refrncr/static/css/components/forms.css
M src/refrncr/static/css/components/tables.css
M src/refrncr/static/css/layouts/app-shell.css
M src/refrncr/static/css/layouts/auth.css
A src/refrncr/static/images/02-animated-grow-shrink.svg
A src/refrncr/static/images/06-favicon-16px.svg
A src/refrncr/static/images/07-static-logo-256px.svg
D src/refrncr/static/images/logo-horizontal.svg
D src/refrncr/static/images/logo-mark.svg
A src/refrncr/static/js/icon-picker.js
A src/refrncr/static/js/key-generation.js
M src/refrncr/static/js/refrncr.js
M src/refrncr/static/licenses/ASSET-SOURCES.md
M src/refrncr/templates/base.html
A src/refrncr/templates/components/dataset_icon.html
A src/refrncr/templates/components/export_access.html
A src/refrncr/templates/components/field_metadata.html
A src/refrncr/templates/components/relationships.html
M src/refrncr/templates/layouts/auth/base.html
M src/refrncr/templates/layouts/page.html
M src/refrncr/templates/pages/auth/login.html
M src/refrncr/templates/pages/datasets/_record_table.html
M src/refrncr/templates/pages/datasets/create.html
M src/refrncr/templates/pages/datasets/detail.html
M src/refrncr/templates/pages/datasets/edit.html
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
A tests/browser_ui_polish_app.py
M tests/dataset_helpers.py
A tests/javascript/key_generation.cjs
A tests/javascript/ui_polish_browser.cjs
M tests/test_concept_migration.py
M tests/test_configure_overview.py
M tests/test_dataset_browser.py
M tests/test_dataset_governance.py
M tests/test_dataset_version_migration.py
M tests/test_interfaces.py
A tests/test_key_generation_javascript.py
M tests/test_migrations.py
M tests/test_new_version_import.py
A tests/test_ui_polish.py
```

## Visual follow-up: duplicate icon controls

The general form `select { display: block }` rule overrode the native hidden state
of the enhanced icon picker's fallback select. The shared icon component now
explicitly hides `[data-icon-select][hidden]`. No tokens, page rules or product
behavior changed. The Chrome test now asserts that the fallback select is hidden
and exactly one icon combobox is exposed after initialization. Checks passed at
320/768/1440 pixels; without JavaScript the native select remains visible.

## Visual follow-up: Dataset identity alignment

The Dataset-table variant groups the name and key beside the decorative icon,
using existing spacing and vertical centering across both text lines. No tokens,
global controls or functional behavior changed. Chrome measurements confirm the
icon is left of the text with vertical centers within one pixel at 320/768/1440
pixels; the existing workflow checks also passed.


## User-approved follow-up: manual Lucide names

This supersedes the initial eight-icon-only validation and native-select design.
Users can type a canonical Lucide name directly; the eight entries are suggestions.
Names are trimmed/lowercased and checked against the unchanged official Lucide
0.468.0 sprite (1,544 symbols), bundled locally. Unknown names store null and render
the default Table icon. Malformed values/URLs/markup and values over 64 characters
are rejected; rendering independently validates names against the same registry.

The single named text input supports manual submission with and without JavaScript,
keeps typed values on Tab/blur, supports keyboard suggestions, and avoids duplicate
controls. Dataset-list and context icons use the one fixed local sprite URL. No new
runtime dependency, external request, arbitrary SVG or dynamic file path is introduced.

Migration 0020 follows the unchanged 0019 and widens icon storage from 32 to 64
characters because two valid pinned names are longer than 32. Existing values are
preserved; downgrade fails closed if long names would be lost. CLI preflight guards
recognize the new head and historical fixtures retain their explicit old-schema paths.

Focused verification: **173 passed** on SQLite/PostgreSQL, including manual names
outside the suggestions, long names, unknown fallback, malicious rejection,
rendering, legacy fallback and migration round trips/guard. Chrome passed at
320/768/1440 px for typing, Tab, saving, reopening and Dataset-list rendering;
unknown input renders Table. A separate no-JavaScript save check passed. Ruff lint
and formatting passed on 12 affected Python files. Full regression remains part 2.

Additional new files: `src/refrncr/static/images/lucide-0.468.0.svg` and
`src/refrncr/migrations/versions/0020_ref_162_icon_names.py`. Changed follow-up files:
`presentation_metadata.py`, `models.py`, `cli.py`, `web.py`, `dataset_icon.html`,
`icon-picker.js`, `forms.css`, icon/browser/migration tests and these developer/asset
notes. No design tokens or page-specific layout rules changed in this follow-up.


## Visual follow-up: Versions page actions

The Versions page no longer renders Concept actions or its unused discard panel.
Its row-level Review link remains. This is a page-specific presentation change;
shared Concept actions on other pages and service authorization remain unchanged.


## User-approved follow-up: edit on Configure

Dataset details now has an inline edit mode with stacked Name, Description, Dataset
icon and Owning team controls, plus Save dataset and Cancel. Read mode also names
the selected icon. Fields, existing inline Access management and the Danger zone
follow vertically. Delete dataset opens the existing exact-name confirmation;
Cancel/Escape preserve inline draft values and restore focus. The immutable key
remains read only. No tokens, component variants, schema, service authorization,
CSRF policy or Record-page action order changes were needed.

The existing POST route still performs the atomic update. Its success returns to
Configure read mode; validation/conflict errors return Configure edit mode with
submitted values. The legacy `/edit` GET uses the same shared page renderer.
One `_edit_form.html` fragment replaces the removed `edit.html` panel template.
Audit read traces include the configuration reads used by edit/error rendering.

Focused tests: the broad affected run had 344 passes and four stale expectations
(panel rendering, migration head and read-trace inventory); all four are updated
and pass in a 62-pass targeted rerun (one PostgreSQL-only case skipped). The tests
cover edit/read mode, cancellation, preserved error values, CSRF, denial and the
existing ownership/deletion behavior. Chrome passed at 320/768/1440 px, including
Name/error focus, save/cancel, one picker, preserved values when cancelling delete,
no overflow/duplicate IDs, and no-JavaScript saving. No full regression or CI was
run. Package template inventory now expects `_edit_form.html`.

Consistency review: a single Dataset form remains; no edit-panel composition or
obsolete trigger is retained on Configure. Existing controls/tokens and the shared
confirmation panel are reused. The only new page rules are inline edit/read mode
and Danger zone placement. Full regression and definitive review remain part 2.

The wheel/sdist template checks also passed (2 tests). Configure's inline form
uses existing spacing to separate its Save/Cancel row from the last control.


## User-approved follow-up: Configure tabs

The four Configure sections now use tabs: Dataset details, Fields, Access management
and Danger zone, with the same conditional visibility as their existing sections.
This supersedes displaying all sections vertically when JavaScript is enabled.
The shared section-tab style supplies the border and active green underline. New
files: `templates/components/section_tabs.html` and `static/js/section-tabs.js`.
No design tokens, schema, authorization or product actions were added.

The controller is repeat-safe and supports left/right/Home/End, roving tabindex,
labelled panels, hash restoration and native anchor fallback. Panels stay in the
DOM so unsaved Dataset details survive tab switches and delete cancellation.
Fields create/edit/delete/reorder redirects select Fields; the query survives
fetch redirects and the fragment supports no-JavaScript navigation. The missing
identifier warning belongs inside Fields. Form/save/error handling is preserved.

Verification: 208 focused tests passed (32 PostgreSQL cases skipped; no database
behavior changed), Ruff and diff whitespace checks passed. Chrome passed at
320/768/1440 px with keyboard navigation, reload restoration, draft preservation,
Field-save tab restoration, inline access, danger confirmation cancellation and
no-JavaScript save. The Fields view was also visually inspected. Full regression
and independent CI remain deferred to part 2. Nothing staged, committed or pushed.


## User-approved follow-up: version-independent Dataset configuration

The product owner explicitly approved current, version-independent name and description
on 2026-09-14. [ADR-017](../architecture/adr-017-current-dataset-metadata.md) records
this supersession of the metadata portions of ADR-014/015. Name, description and
icon can be saved without a Concept. Metadata changes preserve content revisions,
Concept identities and historical snapshots, survive create/discard, and do not
make a version publishable. Publication still captures metadata as provenance.
No additional migration, dependency, public API or authorization policy change.

The overview's separate gear link has tooltip `configuration` and opens
`/datasets/<key>/configure`. The name still opens Records. Fields is visible but
disabled on general configuration; explicit Concept/version configuration retains
its Fields view. Details, access and deletion use existing current authority.
The legacy edit URL opens general inline editing; save/cancel return there.
Standalone export-access forms retain the configuration context on redirect and refresh.

Classification/consistency review: no tokens changed; shared section-tabs supports
disabled native buttons and keyboard skipping; the shared row-action macro accepts
an optional chevron. Only Dataset rows use a gear with a separate focus target.
General Configure is a deliberate page rule. Current metadata is the approved
functional change. No duplicate composite markup or new framework was introduced.

Affected implementation: `services.py`, `ports.py`, `dataset_versions.py`,
`dataset_diff.py`, `concept_persistence.py`, `web.py`, audit registration/inventory,
Dataset list/detail/edit/access templates, row-action/section-tab components,
section-tabs JS and table/configuration CSS. Focused lifecycle fixtures now use
real Field changes where their purpose is to test publication or stale revisions;
metadata-only fixtures no longer imply a version difference.

Focused verification (batches overlap; no full-regression claim):

- UI, Dataset browser, Configure, governance, Concept lifecycle/discard: 314 passed,
  137 PostgreSQL cases skipped in that SQLite run; 4 large cases deselected.
- SQLite version and diff tests: 115 passed, 111 PostgreSQL cases deselected.
- PostgreSQL UI metadata, versions, diff and Concept lifecycle/discard: 213 passed,
  245 other/large cases deselected.
- New standalone configuration/authority/access and metadata lifecycle selection:
  6 passed, 2 PostgreSQL cases skipped (metadata cases passed in the PostgreSQL batch).
- Targeted import lifecycle, deletion staleness and content freshness on both engines:
  16 passed, 195 unrelated cases deselected.
- Route inventory test: 1 passed, 1 PostgreSQL case skipped. Audit guard verifies
  143 operations and 65 routes. Ruff, formatting of touched Python files and diff
  whitespace checks pass.
- Chrome 152: 320/768/1440 px, independent gear navigation, disabled Fields,
  keyboard skipping and disabled hash handling; existing inline edit, field saves,
  access refresh, draft preservation, icon fallback, responsive and no-JS checks pass.
  No page errors or external requests; configuration screenshot visually inspected.

Full regression, pre-commit and independent CI remain for part 2. No staging,
commits, pushes or external Jira/Confluence updates were made.


## User follow-up: Fields remains viewable

General Configure now enables Fields as a read-only tab, superseding its disabled
state above. It reuses the existing authorized Field selection: an active Concept
for maintainers, otherwise Current. The source is labelled within the tab. No
available Current/Concept leaves general metadata configuration accessible with
an explanatory Field state. Add/edit/order controls remain absent, including for
administrators; explicit Concept configuration retains existing edit authority.

Only page composition and data reads changed; no tokens, shared components,
schema, dependencies or permission policy changed. The existing tab controller
supports keyboard navigation and direct `#fields` links unchanged. Documentation
and the audit route/read inventory have been updated.

Verification: 80 focused Python tests passed (79 UI/Configure plus one reader
isolation test); 34 PostgreSQL cases skipped, no persistence changes in this follow-up.
Ruff, audit inventory (143 operations/65 routes), formatting and diff checks pass.
Chrome passed at 320/768/1440 px, including enabled read-only Fields, keyboard and
hash navigation, existing workflows and no-JavaScript fallback. The mobile Fields
screenshot was visually inspected. Full regression/CI remain deferred to part 2;
nothing staged, committed or pushed.
