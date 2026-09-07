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
Collapsible navigation below 1024 px belongs to REF-128 and is not part of this foundation.

Navigation visibility improves clarity but is never authorization. Every route and mutation
must continue to enforce server-side permissions and CSRF independently.

## CSS loading order

`static/css/refrncr.css` is the only public stylesheet entrypoint. It imports, in order:

1. primitive and semantic tokens;
2. element defaults and accessibility utilities;
3. the app-shell, page and authentication layouts;
4. buttons, forms, feedback, cards/badges/empty states, tables/pagination and dialogs.

Imports are relative to the entrypoint and all nested files are package data, so the same URLs
work from a source checkout and an installed wheel. Breakpoint literals in media conditions must
stay aligned with the reference values documented in `tokens.css`; custom properties cannot be
used as media-query boundaries.

Broad implicit component selectors are intentionally avoided. Forms that need the shared width
contract use `.form`; buttons use `.button` plus a variant; hover behaviour for table rows is
scoped to `.table-scroll`. Plain semantic elements remain available when no component treatment
is intended.

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
