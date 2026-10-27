# Changelog

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

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
- Change the project license from Apache-2.0 to the Elastic License 2.0 and require a
  Contributor License Agreement for contributions.
