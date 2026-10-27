# UI foundation

## Purpose and source of truth

This document describes the current Community UI contract for refrncr. The
templates, CSS, JavaScript, automated tests, and accessibility behaviour in
the repository are authoritative; this document explains the patterns they
are expected to follow.

The UI is server-rendered. Routes prepare presentation data, services own
business rules, templates own markup, and the shared CSS and JavaScript own
reusable presentation and interaction behaviour. New screens should extend
these existing patterns instead of creating parallel conventions.

## Brand, typography, and assets

- Brand assets and fonts are local repository assets. Do not add runtime CDN
  dependencies for the application shell.
- Use the existing font family and weights from the static asset bundle.
- Use semantic colour variables rather than literal colours in page-specific
  styles.
- Icons must retain an accessible name when they communicate meaning. An
  icon-only control needs a visible or assistive label.

## Design tokens

The shared tokens live in `src/refrncr/static/css/tokens.css`. Components and
pages should consume those variables for colour, typography, spacing,
radius, shadows, and motion.

The responsive layout uses the existing breakpoints at 320, 768, 1024, and
1440 pixels. The 320-pixel layout is a supported minimum viewport: content
must remain readable without horizontal page scrolling.

When a repeated visual value is introduced, first decide whether it is a
design token, global component rule, component variant, or a genuinely
page-specific rule. Put it at the highest level that accurately describes
the behaviour. Avoid duplicating the same value in templates or inline
styles.

## Application shell and layout

`src/refrncr/templates/base.html` owns the global shell:

- the skip link and primary landmarks;
- the brand and primary navigation;
- authenticated account context and the CSRF-protected logout form;
- the page title and main content region;
- global feedback and footer structure.

Authenticated error pages use the same shell when an actor is available, so
the user retains navigation and account actions while an error is explained.
Unauthenticated errors remain safe and minimal.

Settings pages use the shared settings shell and navigation. Page templates
should extend the appropriate base template and provide semantic content
blocks; they should not reproduce the application shell.

## Shared components

Reusable macros and includes provide the common presentation vocabulary:

- page headers and breadcrumbs;
- buttons and button variants;
- labelled form fields, help text, and validation feedback;
- alerts and status feedback;
- search fields;
- data-table rows and empty states;
- side panels and record toolbars.

Use the existing component API and classes before adding a new selector. A
new component is justified when the interaction or semantic structure is
reused in more than one workflow. Keep component markup in templates and
macros, not in Python strings.

### Change-status language

The shared change-status vocabulary is **Added**, **Changed**, and **Deleted**.
The domain enum and its presentation mapping own the labels and lifecycle badge
variants; templates should render change badges through `status_badge`. The
persisted `Removed` value maps to the visible label Deleted. The former filter
value `Edited` is accepted only for backward-compatible links and is presented
as Changed. Do not introduce a second translation or hand-built change badge.

## Forms and error handling

Server-side validation is authoritative. A failed submission must return the
form to the relevant context, preserve ordinary submitted values in escaped
form controls where doing so helps correction, and associate each error with
its field using semantic labels and accessible error text.

Passwords, API keys, session tokens, and other secrets must never be echoed
back into HTML, logs, or error messages. Error messages must not expose
unnecessary internal identifiers or sensitive implementation details.

Use the established status semantics:

- 400 for invalid input or malformed requests;
- 403 for a known actor without permission;
- 404 for a missing resource or a deliberately non-disclosing response;
- 405 for an unsupported method;
- 409 for a recoverable state conflict;
- 500 for an unexpected application failure.

Error responses use the normal application error page. Unexpected failures
are logged with a fixed diagnostic message and traceback for operators, while
the browser receives a safe explanation and recovery action.

## Accessibility

Every page has one clear `h1`, meaningful landmarks, visible keyboard focus,
and labels for interactive controls. Use native buttons and links for their
respective actions. Do not rely on hover, colour, or an icon alone to convey
state.

Interactive controls must remain usable with keyboard navigation and touch
input. The shared control sizing targets a minimum 44-pixel hit area where
the component allows it. Tables use captions and appropriate header scope;
status and validation feedback use the established live-region behaviour.

If visual text is truncated, the complete value remains available to
keyboard and assistive-technology users. Responsive layouts must not hide
required actions from those users.

## Responsive navigation

The navigation has two modes:

- At desktop widths it is expanded by default and may be collapsed by the
  user. The preference is stored only in the existing browser session state.
- Below the desktop breakpoint it is presented as a native dialog drawer with
  focus management, an explicit close action, and restoration of focus to the
  invoking control.

The behaviour must remain correct when the viewport crosses the breakpoint.
Both modes use the same arrow tab at the navigation edge: right to open, left to close.
The visual tab stays narrow while the button has a 44 × 44 px target and a visible keyboard
focus ring. Reserve space above the content so the opener target does not cover it.
The mobile app shell has no separate logo/page-title bar; the logo remains in the navigation.
Settings shares the mobile drawer controls but keeps its fixed, non-collapsible desktop sidebar.

Do not persist a server-side navigation preference for this client-only UI
state.

## Tables, side panels, and record workflows

Data tables remain readable at narrow widths and expose the record identity,
primary action, and relevant status without requiring hover. Row actions use
the established confirmation and feedback patterns.

Side panels preserve the page context and expose their state in the URL or
form target where the workflow requires it. When a form validation error is
returned, the panel containing the invalid form opens automatically. A
membership error remains in the Team members context; it must not silently
move the user to an unrelated team form.

Record workflows keep destructive actions visually and semantically distinct,
require the existing confirmation step, and preserve server-side
authorization regardless of whether an action is visible in the UI.

## Implementation and verification

Before adding a page-specific style, check the shared tokens, macros, and
component variants. Keep routes focused on request handling and presentation
data. Preserve escaping, CSRF protection, authorization, IDOR protection,
transaction boundaries, and audit behaviour.

UI changes should include focused tests for the affected workflow and
regression coverage for validation, permissions, responsive state, and error
handling where applicable. Verify the supported viewport widths, especially
320, 767, 768, 1023, and 1024 pixels. Run the repository's configured
pre-commit, pytest, and Ruff checks before considering the change complete.
