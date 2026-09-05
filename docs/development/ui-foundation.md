# REFRNCR UI foundation

REF-122 establishes the shared visual foundation for the Community application. Cloud may
reuse this foundation, but edition-specific functionality must not fork these tokens or
components.

## Assets and typography

The canonical logo is the rounded 2×2 grid with the filled top-left square. Use
`logo-horizontal.svg` in application chrome and `logo-mark.svg` where only a compact mark
fits. Do not recolour, distort, rotate, or add effects to either asset.

The font stacks are intentionally offline-safe:

- Montserrat 500 for the wordmark and page-level headings;
- Inter 400–700 for interface text;
- JetBrains Mono 400 for technical identifiers and code-like values;
- system fonts are the fallback when those fonts are not installed locally.

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
and main landmark. Authenticated pages are rendered inside this shell.

Reusable classes include:

- `button-primary`, `button-secondary`, `button-danger`, and `button-link`;
- standard text, select, checkbox, file and textarea styling;
- `help-text`, `error-text`, and `alert-*` feedback;
- `card`, `badge-*`, `table-scroll`, `pagination`, and `empty-state`;
- `dialog` and its open/close data attributes for confirmations.

Shared Jinja patterns for alerts, empty states and confirmation dialogs live in
`templates/components.html`. Component names use lowercase kebab-case. State classes use the
`is-` prefix, and JavaScript hooks use `data-*` attributes rather than presentation classes.

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
