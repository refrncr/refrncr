# Changelog

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

- Reload the authoritative access-management page after an uncertain export-access
  grant or revoke failure, including when the safe GET target is already open (REF-320).
- Move focus into asynchronously opened side panels and preserve it across in-panel
  refreshes, with a panel-title fallback when the focused control disappears (REF-321).
- Let record search controls wrap naturally when space runs out, keeping mapping and
  hierarchy controls and the hierarchy column menu usable at narrow widths (REF-317).
- Preserve keyboard focus on the chosen source-format control while refreshed import
  examples replace its markup (REF-318).
- Keep Tab and Shift+Tab navigation moving through the form when an open enhanced select closes (REF-316).
- Disable view transitions on pages without JavaScript, preventing console errors
  during navigation such as signing in (REF-324).

- Handle expected cross-document ViewTransition cancellations in the application (REF-311),
  preserving instant no-flash navigation and API-key snapshot exclusions. Add mandatory
  browser coverage for criteria, history, panels and reduced motion without test-side masking.

- Simplify audit time filters and list times to minute precision (REF-257). Preserve
  exact event details and untouched filter links, including daylight-saving offsets.

- Use the same navigation arrow tabs on desktop and small screens (REF-236), with
  generous touch targets. Remove the mobile app topbar and share the drawer controls
  with Settings while preserving its fixed desktop sidebar.

- Require safe Excel XML parsing as a runtime dependency (REF-48). Refuse webapp
  startup before migrations and direct Excel imports when protection is unavailable.
  Preserve import limits and export contracts; verify a runtime-only wheel installation in CI.

- Enforce same-origin CSP and browser security headers on the webapp (REF-49), including
  frame protection, HTTPS-only HSTS and nosniff on downloads. Add a mandatory CSP browser
  smoke test while preserving the separate API and documentation policies.

- Fix individual API reads of hierarchy records without business identifiers (REF-309).
  Identifiers from record lists and hierarchy snapshots now resolve within the selected
  version, preserving the existing response, authorization and conditional-read contracts.

- Add published mapping lookup by typed source value, mapping field roles and pinned source
  version metadata to the API (REF-92). Lookup follows mapping export access and existing
  version selection and caching; unmapped source values return records with null targets.

- For rc1, add Settings → Dataset access for file export and API reads (REF-294/REF-306).
  New and upgraded installations default to Everyone: every active user can consume
  published datasets. Stricter modes require owning-team membership or an explicit grant,
  with administrators retaining access. API keys follow their owner's current rights;
  app reading and API version selection remain unchanged.
- Use “dataset” terminology and “Type” labels consistently; return hierarchy API nodes in a fixed order.
- Centralize change statuses in a domain enum and map them to the Added/Changed/Deleted UI labels.
- Give incomplete and oversized Record identifiers distinct, actionable validation messages.
- Build the return address after sign-in from the application root, so the redirect can never
  leave refrncr; the accepted paths are unchanged.
- Add the Select component: a styled single-choice list with keyboard support, search from
  10 options and upward opening near an edge, built over a native select that still works
  without JavaScript. Every select in the application uses it; in the column mapping, Do not
  import now sits directly under Choose a field.
- Improve the first-run developer documentation and test foundation.
- Require a Contributor License Agreement for contributions.
