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

The required upstream WOFF2 files and their OFL notices still need to be packaged in the
dedicated asset step. Until that work is complete, the named fonts are only preferred font
families and the application may use the system fallbacks. The completed foundation must not
request fonts from a CDN at runtime.

The application never requests fonts or icons from a CDN. Inline SVG is appropriate for a
small functional icon; icon-only controls always require an accessible name.

## Tokens

All canonical tokens live in `static/css/refrncr.css` under `:root`. They cover the green,
neutral, success, warning, error and information colours; semantic aliases; the three font
stacks; the 4/8/16/24/32/48 px spacing scale; radii; shadows; and the 320/768/1024/1440 px
breakpoints. New pages must use semantic variables such as `--color-primary` and
`--color-border`. Do not copy token sets into page-specific CSS.

CSS custom properties cannot be used directly in media-query conditions. The documented
breakpoint values therefore remain tokens for tooling/documentation, while media queries use
their corresponding literal values.

## Layout and components

`templates/base.html` owns the document structure, skip link, logo, permission-aware primary
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
shared auth layout, `form_field`, `feedback`, and a real submit-button variant. Credential
failures are shown once as generic form feedback and referenced by both login fields. Neither
submitted login field is rendered back into the response. Setup may safely redisplay the email
and display name after validation, but never the submitted password.

The Dataset-create page combines `page_header`, `form_field`, `feedback`, and link/submit button
variants. Its Description field selects the generic textarea control while retaining the same
label, value, error, disabled, and accessible-description contract as text inputs.

The Dataset-edit page reuses those primitives directly. It presents the immutable Dataset key as
text rather than a submitted control, and connects validation feedback only to the affected field.
Create and edit remain separate page compositions because their key and action contracts differ.

The Dataset-detail page uses a file template under the shared shell. Its existing metadata,
permission-dependent schema controls, field table, and field-order forms remain unchanged.

Remaining pages still use the legacy inline-template adapter and will be migrated incrementally
without changing their route behaviour.

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

At widths below 768 px the sidebar becomes an explicitly controlled menu. Pages must remain
free of horizontal overflow at 320 px; dense tables may scroll inside their own container.
Desktop remains the primary target for dataset workflows.

Navigation visibility improves clarity but is never authorization. Every route and mutation
must continue to enforce server-side permissions and CSRF independently.
