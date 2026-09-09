# REFRNCR UI foundation

REF-122 establishes the shared visual foundation for the Community application. Cloud may
reuse this foundation, but edition-specific functionality must not fork these tokens or
components.

## Assets and typography

The canonical logo is the rounded 2×2 grid with the filled top-left square. Use
`logo-horizontal.svg` in application chrome and `logo-mark.svg` where only a compact mark
fits. Do not recolour, distort, rotate, or add effects to either asset.

The typography contract is:

- Montserrat 500 for the wordmark and page-level headings;
- Inter 400–700 for interface text;
- JetBrains Mono 400 for technical identifiers and code-like values;
- system fonts remain the fallback.

The required upstream WOFF2 files and their OFL notices are packaged below `static/fonts/` and
`static/licenses/`. `static/licenses/ASSET-SOURCES.md` records their pinned upstream versions,
filenames and licenses. The browser loads them through relative local URLs; no font or icon CDN
is used at runtime.

The application never requests fonts or icons from a CDN. Inline SVG is appropriate for a
small functional icon; icon-only controls always require an accessible name.

## Tokens

All canonical tokens live in `static/css/tokens.css` under `:root`. They cover the green,
neutral, success, warning, error and information colours; semantic aliases; the three font
stacks; the 4/8/16/24/32/48 px spacing scale; radii; shadows; and the 320/768/1024/1440 px
breakpoints. New pages must use semantic variables such as `--color-action-primary` and
`--color-border`. Do not copy token sets into page-specific CSS.

CSS custom properties cannot be used directly in media-query conditions. The documented
breakpoint values therefore remain tokens for tooling/documentation, while media queries use
their corresponding literal values.

## Layout and components

`templates/base.html` owns the document structure, skip link and overridable layout block.
Its default application layout owns the logo, permission-aware primary
navigation, current-page state, account context, CSRF-protected sign-out action, flash region,
main landmark, and page script extension point. File-based page templates extend this shell.

Page-level layout macros live under `templates/layouts/`. Reusable controls live under
`templates/components.html` until the component set is large enough to split by component.
The overview page demonstrates the current composition pattern:

```jinja
{% extends "base.html" %}
{% from "components.html" import button %}
{% from "layouts/page.html" import page_header %}

{% block content %}
  {{ page_header("Overview", "Manage your reference data in one place.") }}
  {{ button("View datasets", url_for("dataset_list")) }}
{% endblock %}
```

The current reusable Jinja primitives are:

- `page_header`, for a page title and optional description;
- `button`, for link actions and real buttons using the same variants;
- `form_field`, for a labelled input with optional autocomplete, required, value, help, error,
  disabled, `aria-describedby`, and `aria-invalid` behaviour; the same contract renders an
  input or textarea when a migrated page needs that control;
- `feedback`, for escaped status or error feedback with the appropriate live-region role.

The overview uses `page_header` and both link-button variants. The login and setup pages use the
shared auth layout, `form_field`, `feedback`, and a real submit-button variant. The auth layout
overrides the application shell with a centred canonical wordmark and card. Login opts into the
shared field password-visibility button and focusable feedback summary. The shared external
controller reveals the button only when JavaScript is available, updates its action name and
focuses the error summary after a failed submission. Required-field validation runs on the
server to keep messages in English even without JavaScript. Credential
failures are shown once as generic form feedback and referenced by both login fields. The
submitted email is escaped and retained (bounded to the browser identifier limit); the
password is never rendered back into the response. Setup may safely redisplay the email
and display name after validation, but never the submitted password.

The Dataset-create page combines `page_header`, `form_field`, `feedback`, and link/submit button
variants. Its Description field selects the generic textarea control while retaining the same
label, value, error, disabled, and accessible-description contract as text inputs.

The `readonly_field` macro renders non-editable form values with the shared `.form-label`
typography. Dataset and field edit forms use it for immutable keys; only the value uses
monospace, while the label matches editable field labels in full pages and side panels.

The Dataset-edit page reuses those primitives directly. It presents the immutable Dataset key as
text rather than a submitted control, and connects validation feedback only to the affected field.
Create and edit remain separate page compositions because their key and action contracts differ.

The Dataset-detail page uses a file template under the shared shell. Its existing metadata,
permission-dependent schema controls, field table, and field-order forms remain unchanged.

The Dataset list uses a file template with Dataset, Records and Updated columns. Counts use
the shared `numeric-column` treatment; there are no search, filter or sort controls. Dataset
names remain keyboard-accessible links and pointer row navigation remains an enhancement.
REF-130 adds shared `breadcrumbs` and `content_metadata` macros in `components.html`.
The dataset breadcrumb include supplies Datasets → No owning team → dataset name, with only
Datasets linked and the last item marked as current. Creation uses Datasets → New dataset
because no dataset exists yet. The records and configuration headings consume the same secondary
metadata component. The shared `page_context` layout follows the supplied dataset-table HTML
reference: a flat column-flex group with no breadcrumb/title gap, zero child margins, and
`--space-sm` (8 px) padding before metadata. This reproduces the reference's single collapsed
8 px heading/paragraph margin without relying on margin collapse inside flex layout.
The `.page-title` class shares the canonical heading font family and wrapping rule, using
`--font-size-page-title` (28 px), semibold weight and normal font line-height. Other `h1` styles
remain unchanged. Breadcrumbs use regular 12 px Inter, normal line-height, uppercase presentation
and `--letter-spacing-label` (0.5 px). Breadcrumb links use the lighter `--color-breadcrumb-link` (#2c8156), with 4.55:1 contrast
on the #f9f9f9 background. This closely follows the reference green while retaining AA contrast.
Hover retains `--color-link-hover`; other breadcrumb items and statistics use the shared muted grey. Metadata uses 14 px Inter, normal line-height,
tabular figures and one inline-flex row with 8 px horizontal gaps. The decorative bullet has
`aria-hidden="true"` and is grouped with Updated so it cannot wrap alone. Whitespace between the
count and Updated groups retains a text boundary for assistive technology. The outer header's
24 px bottom margin and responsive action layout remain unchanged. The typography and breadcrumb-colour tokens are
shared; no dataset-specific selectors or component variants are introduced.

The records route uses a file template with page-private table, field, and record-panel
compositions. Its create and per-record workflows use the shared modal side-panel structure;
one rendered panel per displayed record remains a deliberate temporary performance trade-off.

Community user, team, and membership management use file templates and the same modal side-panel
structure. Team membership data is loaded only for the team selected through `open_team`, avoiding
per-team membership queries on the list page while preserving the existing management workflows.

Dataset field, standalone record mutation, and record-import workflows also use file templates.
The legacy inline-template adapter has been removed; non-HTML downloads, redirects, and plain-text
security responses retain their existing response contracts.

Reusable classes include:

- `button-primary`, `button-secondary`, `button-danger`, and `button-link`;
- standard text, select, checkbox, file and textarea styling;
- `help-text`, `error-text`, and `alert-*` feedback;
- `card`, `badge-*`, `table-scroll`, `pagination`, and `empty-state`;
- `dialog` and its open/close data attributes for confirmations.

Component names use lowercase kebab-case. State classes use the `is-` prefix, and JavaScript
hooks use `data-*` attributes rather than presentation classes. Documentation must only list a
Jinja component after a page actually consumes it.

All applicable controls define hover, visible keyboard focus, active, disabled and error
states. Destructive actions use the danger treatment but still include explicit text; colour
is never the only signal.

## Template and accessibility rules

New templates must:

1. keep business logic in routes and services;
2. use one `h1` and a logical heading hierarchy;
3. use landmarks and named navigation regions;
4. associate every control with a `label` and connect help/errors with `aria-describedby`;
5. use `aria-current="page"` for active navigation;
6. give tables a caption (visually hidden is acceptable) and `scope="col"` headers;
7. wrap wide tables in `table-scroll`, never the whole page;
8. preserve visible focus and keyboard access;
9. return focus to a dialog trigger through the native `<dialog>` behaviour;
10. render all user-controlled values through Jinja auto-escaping.

The supported application workspace starts at 768 px and targets tablet and desktop use. Below
768 px access is best effort: navigation, forms, and side panels remain operable, while no
mobile-specific redesign is implied. Pages remain free of horizontal overflow; wide data tables
retain readable minimum dimensions and scroll horizontally inside their `table-scroll` container.
The shared navigation contract below applies at every supported width, including 320 px.

At desktop/tablet widths (768 px and above), the shared app shell is bounded by the viewport
height. Main content scrolls independently so long dataset tables cannot push the account
details, Settings and Log out below the screen. Sidebar navigation can scroll separately
when necessary, while the account area does not shrink. The same viewport layout serves
the dedicated Settings shell. Smaller screens retain page scroll with a temporary navigation drawer.
Navigation focus outlines sit inside each item so scrolling containers cannot clip them.

Navigation visibility improves clarity but is never authorization. Every route and mutation
must continue to enforce server-side permissions and CSRF independently.

## Responsive navigation state (REF-128)

Both authenticated shells use `components/navigation.html`, `layouts/app-shell.css` and
`static/js/navigation.js`. The existing navigation DOM is reused, including active destinations,
permission-aware links, account context and the CSRF-protected sign-out form. Settings retains
its own navigation content. No page-specific controller, token, dependency or account preference
is added. Data tables, record workflows and side-panel controllers retain their existing contracts.

- At **1024 px and wider**, the complete sidebar is expanded by default. Its top-right
  icon-only control aligns with the wordmark (Back to app in Settings). `panel-left-close`
  has accessible name and native tooltip **Collapse navigation**. Collapsing hides the entire
  sidebar and releases its 240 px column to main content. The content header then shows
  `panel-left-open` at its top-left, named and titled **Expand navigation**. Neither action
  navigates or reloads. Desktop has no modal overlay or focus trap.
- Desktop choices use `sessionStorage["refrncr.navigation.desktop"]`, with `expanded` or
  `collapsed` values. This is tab/browser-session presentation state, shared across shell pages,
  never a server/account preference. Missing or invalid values default to expanded. Storage
  failure leaves in-page toggles usable. The small external script runs synchronously in the
  document head, before styles/content, to apply the stored class before first paint.
- **Below 1024 px**, CSS reserves no navigation width regardless of desktop storage. The
  header control is named and titled **Open navigation**. Navigation starts in a closed native
  dialog and opens as a temporary drawer with the existing overlay colour/shadow. `showModal()`
  makes the background inert. Initial focus goes to **Close navigation**; Tab/Shift+Tab wrap
  through visible drawer controls. Escape, the close button or a completed backdrop click
  closes it and restores the exact opener. Activating a destination closes before navigation.
- Crossing 1024 px closes any drawer and moves the same sidebar between the modal host and
  desktop grid. Returning to desktop restores the current desktop choice. Focus moves to a
  visible navigation control if the previously focused control would become hidden. Drawer
  interactions never overwrite desktop state. Pages restored from the back/forward cache reread
  the current session choice. The dialog scrolls vertically on short screens.
- Native buttons supply Enter/Space, pointer and touch activation, 44 px targets and the
  shared visible focus ring. Both expose current `aria-expanded` and `aria-controls` pointing
  to the complete controlled sidebar. Decorative Lucide SVGs use the existing inline macro
  mechanism and pinned asset licence. State changes are immediate, without animation, so
  reduced-motion preferences are respected and no intermediate-width layout is introduced.

`test_navigation_javascript.py` exercises default/manual state, restoration before content,
blocked storage, both breakpoint directions, drawer lifecycle, focus wrapping and restoration.
Shell response tests cover both icons, native buttons, accessible names, tooltips, controlled
IDs and early local script loading. Existing interface/dataset tests retain permission, active
state and sign-out CSRF coverage. DOM doubles do not establish rendered layout or native
browser modality: review 320, 767, 768, 1023 and 1024 px in a browser, plus a wide desktop;
check keyboard operation, background inertness, main-width recovery, cross-page retention,
focus rings, logo/control alignment, overflow and first paint with stored collapsed state.

The optional `tests/javascript/navigation_browser.cjs` regression script asserts real Chrome
layout, native Enter/Space and touch activation, focus trapping/restoration, drawer dismissal,
state retention across actual links and reloads, reduced motion and no horizontal overflow at
all five boundary widths. It requires an externally available Playwright module and browser;
neither is added as a project dependency or mandatory CI gate. Start a disposable local app,
create an administrator through setup, then set `REFRNCR_BROWSER_URL` (loopback origin, no
trailing slash), `REFRNCR_BROWSER_EMAIL` and `REFRNCR_BROWSER_PASSWORD` to that test account.
Run `node tests/javascript/navigation_browser.cjs`. `REFRNCR_PLAYWRIGHT_MODULE` may point to
an existing Playwright installation; `REFRNCR_BROWSER_EXECUTABLE` may select local Chrome.
`REFRNCR_BROWSER_OUTPUT` optionally collects screenshots for visual review. This script reads
and navigates the test app; it does not change account permissions or dataset contents.

## CSS loading order

`static/css/refrncr.css` is the only public stylesheet entrypoint. It imports, in order:

1. primitive and semantic tokens;
2. element defaults and accessibility utilities;
3. the app-shell, page, Settings and authentication layouts;
4. buttons, forms, feedback, cards/badges/empty states, tables/pagination and dialogs.

Imports are relative to the entrypoint and all nested files are package data, so the same URLs
work from a source checkout and an installed wheel. Breakpoint literals in media conditions must
stay aligned with the reference values documented in `tokens.css`; custom properties cannot be
used as media-query boundaries.

Broad implicit component selectors are intentionally avoided. Forms that need the shared width
contract use `.form`; standard buttons use `.button` plus a variant. The REF-144 primary
row control uses `.row-action` for its shared anchor/button presentation. Table hover styling
is scoped to `.table-scroll` or the explicit `.interactive-row` variant. Plain semantic
elements remain available when no component treatment is intended.

## Focused login verification (REF-129)

Automated tests cover routing, form semantics, errors, redirects and security regressions.
`test_login_javascript.py` executes the shared controller when Node is available; this does
not replace a real browser or assistive-technology check. No browser dependency was added.

Manual checks still required when browser automation is unavailable:

1. Open `/` and a protected page while signed out. Confirm the focused login page has no
   application navigation or Overview, and shows the canonical wordmark.
2. At 320 px and desktop widths, check the card, labels, errors and buttons fit without
   horizontal scrolling; repeat with zoom and increased text size.
3. Tab through the skip link, brand link, email, password, Show password and Sign in. Confirm
   visible focus throughout. Activate Show/Hide password with Space and Enter: the value stays
   unchanged, the form does not submit and a screen reader announces the new action name.
4. Submit empty fields and incorrect credentials. Confirm focus reaches the error summary and
   the screen reader associates each invalid field with its error. Email stays populated;
   password stays empty after server responses. Repeat with JavaScript disabled: login and
   server validation still work and the visibility button is hidden.
5. Sign in from a protected page and confirm return to its path. Sign out and confirm protected
   content is unavailable. Verify text, button and focus-state contrast with the shared tokens.

## Configuration side panels (REF-131)

All side panels use `components/side_panel.html` and the shared external controller.
Do not build separate page-dependent dialog markup, CSS variants or controllers. The
`side_panel` macro accepts a unique panel ID, title and title ID, optional description and
optional description ID, and caller content. It generates `aria-labelledby` and, when a
description is present, `aria-describedby` (with a generated description ID if omitted).
Use `.side-panel-body` for scrolling content and `.side-panel-footer` for separate actions.
The header stays fixed; shared form actions remain available while scrolling. Existing width,
spacing, focus and responsive tokens apply, including full-width panels below 768 px.

Dataset creation uses a single `form_side_panel()` on the dataset list. Dataset settings,
field creation and field editing share one instance on dataset detail. Each trigger retains
its existing authorized route as its `href`:

```jinja
{% from "components/side_panel.html" import form_side_panel %}
<a data-panel-url data-dialog-open="configuration-panel"
   href="{{ url_for('new_dataset') }}">New dataset</a>
{{ form_side_panel() }}
```

Render triggers and the host within the existing permission conditions. Multiple hosts need
unique IDs passed to `form_side_panel(panel_id)` and matching `data-dialog-open` values.

The existing full page is the form source: wrap its reusable form content in
`data-side-panel-content` with an English `data-panel-title`. The controller fetches that
same GET route and imports only this content, leaving the document shell and scripts behind.
There are no extra endpoints, duplicate forms, or client-side validation rules. Keep business
logic, authorization, normalization, CSRF, audit and transactions in the existing server
layers. Form actions and hidden CSRF fields remain the originals. Use `novalidate` so server
validation consistently supplies English messages. A `data-panel-cancel` link retains its
fallback destination and closes the panel when enhanced.

POSTs use the same form data and routes. Server validation responses (400/409) replace the
form in the panel, preserving escaped submitted values; successful redirects navigate to the
server's destination. Authentication redirects and failed GETs fall back to full-page
navigation. Other POST errors retain the form and announce an error; network failures do not
automatically retry mutations. Stale GET/validation responses cannot overwrite a newly opened
form. Only one mutation per panel may be pending at a time.

Opening calls native `showModal()`: the browser supplies background inertness, Tab focus
containment and Escape closing. Focus moves to a visible error summary, an invalid control,
or the first relevant visible form control; the close button is the fallback. Error summaries
use `data-error-focus` and `tabindex="-1"`; invalid controls retain their accessible error
references. Closing through Cancel, the close button or native Escape restores the exact
trigger, including when several triggers share a host. If that trigger has disappeared,
focus returns to the main content. Controller registration is idempotent. Without JavaScript
(or native dialog support), links and full-page forms continue to work.

`test_dataset_browser.py` covers rendering, shared composition, permission-aware triggers,
full-page forms, server mutations, validation, CSRF and escaping. `test_side_panel_javascript.py`
uses the existing Node test approach for control events, focus selection/restoration,
multiple triggers/panels, loading, submissions and errors. DOM doubles verify that native
modality is requested; they cannot prove browser Tab containment, Escape handling or layout.
Before merge, verify in a real browser:

1. Open each of the four flows with the keyboard and confirm the first form control receives
   visible focus. Cycle Tab and Shift+Tab, including from the last control, and confirm focus
   stays inside the dialog and the background cannot be activated.
2. Close with Escape, Close panel and Cancel; confirm focus returns to the particular opener.
   Repeat across different field triggers and existing record/user/team panels.
3. Submit invalid values, correct them and save; check error announcements, focus, retained
   values, successful redirects and existing field deletion behavior.
4. Test desktop, tablet and 320 px widths, short viewports, long labels/keys, zoom and increased
   text size. Check header/close visibility, scrolling, actions and absence of page overflow.
5. Disable JavaScript and repeat all four workflows using the full pages.

Dataset and field edit panels use `panel_form_actions` for the same fixed footer as record
panels. Dataset Save fills the single-column footer; field Save and Delete share the two-column
footer. These edit panels have no Cancel action; the header close button and Escape close them.
Footer submit buttons use native `form` attributes to preserve their original form and CSRF
association, including on full-page fallbacks. Creation panels retain their Cancel action.

## Community Settings (REF-132)

`layouts/settings.html` overrides the base layout to provide a dedicated Settings workspace
for Users, Teams and the standalone membership page. It reuses the app-shell grid and sidebar
width, shared sidebar styling, vertical navigation, management headers and existing page
content. The normal application sidebar and account controls are not rendered in Settings.

Back to app opens Overview and sits in the sidebar heading beside the shared collapse
control. The Settings icon/title stays in the content header, beside the opener when collapsed.
The sidebar contains only Access management, Users and Teams. Active navigation uses the
existing green surface, foreground and `aria-current="page"`. Main content fills the remaining
width, with one Users or Teams heading and no redundant breadcrumb or horizontal Settings tabs.
Settings follows the same REF-128 desktop/session and temporary-drawer contract as the normal
application shell; destinations, permissions and the distinct Settings context are preserved.

`components/flash_messages.html` supplies the same live region to both shells.
`components/icons.html` shares the existing Settings cogwheel between the account link and
Settings header and provides small decorative line icons for Back to app, Users and Teams.
The existing Users/Teams tables, actions, membership controls and shared side panels remain
the page content; routes, form actions, CSRF, sessions, authorization and persistence do not
change. The Settings account link opens `/users`; `/teams` and membership URLs remain intact.
Visibility uses the session-resolved actor's existing `role == 'admin'` condition, independently
of route-level `require_admin()` and service authorization.

The normal app account area retains the refined Settings and Log out links. They share Inter,
regular weight and `--font-size-sm` with the role label, `1em` icons, a 6 px
`--account-link-icon-gap`, and no button surface or underline. `--color-account-link` supplies
the muted blue-grey foreground. Links have no extra vertical padding, with 3 px above Settings
to distinguish account details from actions. Log out remains a CSRF-protected POST button.

`test_interfaces.py` covers the dedicated shell, absence of normal navigation, navigation
contents and active state, page titles, compact menu semantics, account-link visibility,
direct access restrictions and existing management regressions. `test_dataset_browser.py`
covers the stylesheet import contract. Before merge, verify both pages at desktop and compact
widths, including the menu, keyboard focus, Back to app and existing side-panel flows.

The shared `.management-table` scroll container establishes a positioning context for
visually hidden headings at narrow widths. Wide tables scroll within their own region.

The Users table has a `user-management-table` variant below 768 px. Each body row is a
single flexible column with the bold primary Name button above Email address. The shared
REF-144 chevron occupies reserved space at the trailing row edge; no View or Actions column
remains. Role and Status retain their existing mobile visibility rules. Values wrap with
`overflow-wrap: anywhere` for unbroken content. The visually hidden desktop header retains
its column headers, explicit cell/header associations and caption. Explicit table roles
preserve semantics when CSS changes the display layout.

The shared mobile side-panel header reserves a fixed close-button column at the top right
and lets the title wrap in the remaining space. Compact header padding uses the existing
spacing token. Full-width fields, scrollable body, footer and focus behavior are unchanged.

## Record import wizard (REF-134)

The import workflow keeps the dataset shell and breadcrumb context. Its shared wizard template
composes five step includes, an ordered progress indicator, and one navigation footer. Desktop
content scrolls within the available workspace, keeping the footer at the same height on every
step. On narrow viewports the page scrolls and the footer remains sticky.
`layouts/import.css` contains deliberate workflow-specific layout rules; existing color, spacing,
typography, radius, button, field and table tokens are reused without adding tokens. Completed,
current and future steps have text labels as well as color. Automatic worksheet completion stays
visible and noninteractive. The progress list becomes vertical on narrow viewports; tables scroll
inside their named keyboard-focusable region.

`.dialog-large` is a shared dialog size/layout variant with a fixed heading/Close area and a
separately scrollable report body, nearly full-screen below 768 px. The report consumes the existing
open/close controller, native modality and Escape handling, adds explicit Tab/Shift+Tab wrapping,
and restores the exact opener on close. Validation summaries reuse `feedback` and `data-error-focus`.
Mapping errors are associated with their relevant select; the completeness summary identifies
missing required and identifier fields. Client-side completeness and duplicate-submit guards are
progressive enhancements; the server independently checks all transitions and confirmation.
No wizard animation is introduced, and existing reduced-motion tokens remain effective.

For visual review, check all five steps, success and invalid-preview/report states at desktop and
narrow widths. Check long values, mapping examples, preview table scrolling, sticky action placement,
Tab/Shift+Tab containment, Escape/Close focus restoration, and reduced-motion mode. The native file
control remains keyboard accessible and supports dropping files onto the control itself.

The mapping instruction, file/worksheet metadata, live completeness summary, missing-field badges
and continuation hint are inside the accessible `i` disclosure beside the Map columns heading.
The mapping list follows the heading directly. The information panel uses the existing tooltip
controller and supports keyboard focus/scrolling within its content; Escape returns focus to the `i`.
Summary counts and missing fields keep updating while the panel is closed. The mapping page uses a page-specific compact list: source name/examples, a decorative arrow,
and the target control. Rows use the existing 3rem record-row minimum, 14px text, 12px examples, compact 36px
controls and reduced padding, and grow with their content. Below 768px source and target stack;
controls retain 44px touch targets and 16px text. These density rules are scoped to the mapping
list and do not change global fields, badges or tokens. Desktop target cells reserve an 8rem
status column so dropdown widths stay equal across all rows and all status badges align at the
right edge. On mobile the full-width dropdown sits above its right-aligned badge. Existing controls, badges and tokens are reused without global
component changes. Selected mapping status is independent of automatic suggestion confidence;
confidence always names its suggested field. Proposal reasons and confidence are behind the existing mapping status badge (Mapped, Needs a
choice, Do not import, or Mapping error), without an additional badge: native `details` provides a no-JavaScript
fallback, enhanced to a viewport-contained tooltip on hover, focus or touch/click. Escape, blur
and outside clicks dismiss it. The tooltip names the suggested target independently of the selected
mapping. Warnings appear once inside that same tooltip. Ambiguous suggestions use the dropdown
placeholder `Multiple matches — choose one`, with no separate hint below the dropdown. Dropdown options retain their values and short field names. Fields used by another row appear
in a muted `Already mapped` group at the bottom, while the current selection stays in its normal
group. JavaScript updates these groups immediately and restores freed fields in their original
proposal/dataset order. Used fields remain selectable; duplicate validation stays unchanged. The live completeness summary includes identifier fields and updates missing
field badges. Server errors retain their select associations. Truncated examples offer a native,
keyboard-accessible disclosure of the same first three non-empty, non-formula sample values;
full values are escaped and wrap within the row. No mapping or import rules change.

## Configure dataset overview (REF-142)

The overview composes the shared dataset context, header action layout and configuration
side-panel host with two labelled sections: Dataset details and Fields. The product owner's
visual refinements replace the original separate Record identifier section with shared warning
feedback, shown only when fields exist and no identifier is configured. The warning adds no
mutation controls. Empty datasets and configured identifiers produce no warning.

Owning team deliberately reads **No owning team**, matching the shared breadcrumb. The current
model has an initial owning user but no dataset-to-team ownership relation; the product owner
approved this placeholder. A future ownership story must supply an authorized team display name.
Do not substitute a user name or expose internal UUIDs. Ordering forms retain UUIDs only in the
functional hidden inputs required by the existing endpoint.

The table has Identifier, Field, Key, Type, Constraints and Actions columns in canonical field
display order. Identifier and Key headings are visually hidden but available to assistive
technology. The compact identifier column shows the existing decorative key SVG beside hidden
text naming its field. Constraints shows Required, Unique and Identifier badges, or None when
no constraint applies. Keys are muted monospace values in their own column.

Actions and their heading are right-aligned. Edit field uses the shared `file_pen_icon` macro
without a button border or background, retaining the existing authorized route, accessible
field name, keyboard focus and minimum click area. Up/down controls preserve their existing
forms, permissions and programmatic boundary-disabled states. The Fields header retains one
Add field trigger in both populated and empty states.

`layouts/configuration.css` supplies the overview and field-table variant. No tokens, service
contracts, domain rules, persistence operations or dependencies were added. The explicitly
requested extra 10 px above Dataset details is a deliberate page-specific spacing exception.
Dataset key padding is removed to align it with other detail values. Other values reuse existing
semantic tokens. The table uses its own keyboard-focusable horizontal scroll region; metadata
remains untruncated, wraps within its region and uses ordinary Jinja escaping.

`test_configure_overview.py` covers viewer/editor/admin presentation, zero/single/multiple fields,
conditional identifier warnings, canonical ordering after reorder, separate key cells, constraints,
icon-only editing, boundary controls, internal-ID exclusion and malicious input escaping.
Existing dataset browser and side-panel controller tests retain authorization, CSRF, not-found
and panel lifecycle regression coverage.

Final local verification used Chrome at 1440, 768 and 320 px with populated, empty and long/malicious
metadata fixtures. Six semantic columns, hidden Key heading, 48 px identifier column, separate
name/key cells, right-aligned actions and borderless file-pen presentation were verified. No
page overflow occurred; wide tables scrolled internally. Keyboard order, visible focus, reachable
actions and read-only viewer presentation were checked. All three panel entry points opened with
initial field focus and restored their invoking control after Escape or Close. Successful dataset
submission returned to the updated overview. The browser test waits for the native close event
before opening another panel; Chrome may include browser chrome in native modal Tab cycling.

CI's PostgreSQL 16 gate remains pending until the change is pushed after visual review. Local
integration tests use PostgreSQL 15. No actual dark theme or assistive-technology session was
verified; the implementation retains semantic tokens and accessible markup.

## Primary table-row actions (REF-144)

A navigational table is a read-only listing whose ordinary item rows each have one clear
primary detail destination: either an existing page URL or an existing JavaScript side panel.
The row action must preserve that exact destination and its server-side authorization.

`components/table_row.html` provides `table_row` and `row_action`. Wrap the existing cells in
`{% call table_row(...) %}` and render one `row_action` in the item's identifying cell.
Pass `href` for an existing URL, or `panel` for an existing side-panel ID; use `panel=true`
on the row for the existing selected-panel state. `search_text` preserves list filtering,
and `explicit_role=true` preserves the user table's responsive semantic roles. The action
accepts escaped label text or caller content, plus an optional descriptive accessible name.
Its decorative chevron sits at the trailing edge of the row without adding a column or tab stop.
Keep any other legitimate actions in their own cells.

The shared variant applies to the dataset and team lists (anchors) and user and record lists
(panel buttons). The dataset-fields table is configuration and retains its explicit Actions
column. The import-preview table is non-interactive. Neither uses this variant. Only ordinary
body rows with one primary destination should adopt it; omit it for empty, summary, selection,
bulk-action or equally ranked multi-destination rows.

`components/tables.css` shares hover, active and visible focus treatment between both controls.
It reuses existing semantic tokens; the hover layer preserves underlying row backgrounds and
the selected-row marker. Underlining and an inset row focus outline supplement colour. The
existing table-region scroll containment and the user table's compact layout remain in use.

`refrncr.js` delegates unused row-space clicks to the real control. A CSS stretched-control
overlay is deliberately avoided because it would cover selectable text and nested controls.
The controller ignores handled events, modified/non-primary clicks, text selections and other
interactive elements. It adds no row key handler or row tab stop: Enter uses native anchor or
button behaviour; Space uses native button behaviour. Anchors retain native context menus,
modified activation and direct navigation without JavaScript. Modifier clicks on unused row
space are ignored; use the actual link or its chevron for native link actions. Panel buttons
continue to depend on the existing JavaScript side-panel controller, including focus return.
Primary links use `draggable="false"` so dragging text selects it instead of starting a link
drag operation; this does not replace the anchor's URL, context menu or new-tab behaviour.
No new page URLs or server-side contracts are introduced.

Keep valid `table`, `caption`, `thead`/`tbody`, `tr`, `th scope="col"` and `td` structure.
The macros place the control inside a cell, never around a row. Do not add row `tabindex`,
button roles, inline handlers or duplicate controls/names for the same primary destination.
The identifying text is the visible label; optional accessible names must include it, including
numeric display formatting. Jinja auto-escaping applies to labels, search text and attributes.
The SVG chevron has `aria-hidden="true"` and `focusable="false"` and creates no extra tab stop.
Long record values remain available to assistive technology and in the keyboard-accessible
record panel. Keep any nested control's own accessible name, focus styling and authorization.

The component must not produce page-level horizontal overflow at 320 px, tablet or desktop
widths. Keep the `table-scroll` wrapper; deliberately wide record tables scroll internally.
The chevron is reserved within the last cell (or compact row padding), not an Actions column.
Hover underlines the primary label; active uses a double underline, and keyboard focus adds
a 3 px inset outline to the row alongside the primary control's outline. Other row background
states and the selected-panel marker remain intact; no palette or appearance mode is added.

Example inside a table's `tbody` (one primary control per row):

```jinja
{% from "components/table_row.html" import table_row, row_action %}
{% for item in items %}
  {% call table_row() %}
    <td>{{ row_action(item.name, href=item.existing_detail_url) }}</td>
    <td>{{ item.updated_at }}</td>
  {% endcall %}
{% endfor %}
```

For a panel-only item, use `table_row(panel=true, kind='record-row')` and
`row_action(item.name, panel=item.existing_panel_id)` instead. The existing panel must already
be rendered with the shared side-panel component. Retain remaining legitimate controls in
an Actions cell; do not apply either macro to a row with no primary destination or several
equally ranked destinations. Never rely on hiding an affordance for access control.

### Regression verification

`tests/test_table_rows.py` covers the real four list templates, unchanged destinations,
identity and formatted labels, escaping, primary-control counts, column removal/retention,
empty states and the excluded configuration/import-preview tables. The dependency-free
`tests/javascript/table_rows.cjs` and `side_panels.cjs` exercise delegated event guards,
selection, nested controls and panel focus-return contracts. They run through pytest.

`tests/javascript/table_rows_browser.cjs` is an optional real-browser check using the same
external Playwright convention as the navigation regression script. It is not a new mandatory
CI browser framework. Start the disposable in-memory fixture in a separate terminal:

```sh
.venv/bin/python tests/browser_table_rows_app.py
```

Then, with an externally available Playwright installation and Chrome/Chromium executable:

```sh
REFRNCR_PLAYWRIGHT_MODULE=/path/to/playwright \
REFRNCR_BROWSER_EXECUTABLE=/path/to/chrome \
REFRNCR_BROWSER_OUTPUT=/tmp/ref144-browser \
node tests/javascript/table_rows_browser.cjs
```

The fixture binds only to `127.0.0.1:5144`, uses disposable test credentials, and discards its
data when stopped. The script checks all four tables at 1440, 768 and 320 px, native primary
keyboard activation and focus return, selection/copy, row-space clicks, real new tabs and
no-JavaScript anchors, nested controls and pagination. It records screenshots and results
outside the repository when `REFRNCR_BROWSER_OUTPUT` is set. Native OS context-menu item
selection and a real assistive-technology session remain manual checks; a headless contextmenu
event and semantic DOM assertions do not claim those checks were performed.


Local REF-144 verification (2026-09-09): the full pytest run passed 827 tests with PostgreSQL
15.18 enabled and no skips. The row browser script passed 18 scenario groups in Chrome
152.0.7977.83, including hover/active feedback and preservation of selected/warning row states;
the existing navigation browser regression also passed. Representative screenshots were
reviewed at 1440, 768 and 320 px. Ruff and pre-commit checks passed. The mandatory CI gate
with PostgreSQL 16 is still required before merge; no commit or push was performed as part
of this local verification. Actual OS context-menu item selection and a screen-reader session
were not performed.
