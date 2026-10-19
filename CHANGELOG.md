# Changelog

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

- Centralize change statuses in a domain enum and map them to the Added/Changed/Deleted UI labels.
- Give incomplete and oversized Record identifiers distinct, actionable validation messages.
- Build the return address after sign-in from the application root, so the redirect can never
  leave refrncr; the accepted paths are unchanged.
- Add the Select component: a styled single-choice list with keyboard support, search from
  10 options and upward opening near an edge, built over a native select that still works
  without JavaScript. Every select in the application uses it; in the column mapping, Do not
  import now sits directly under Choose a field.
- Improve the first-run developer documentation and test foundation.
- Change the project license from Apache-2.0 to the Elastic License 2.0 and require a
  Contributor License Agreement for contributions.
