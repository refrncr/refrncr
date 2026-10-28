# Browser acceptance tests

The browser acceptance suite exercises the rendered Community application in real Chromium against disposable, loopback-only fixtures. Each `tests/javascript/*_browser.cjs` file states its focused journey at the top and is mapped to a fresh fixture in `tests/run_browser_tests.py`.

## Browser flows

| Flow | What it checks | PR core | File |
| --- | --- | :---: | --- |
| `access_management` | Grants and revokes Access-tab permissions across viewport sizes and color themes. | Yes | [access_management_browser.cjs](../../tests/javascript/access_management_browser.cjs) |
| `api_docs` | Required REST documentation operations, authorization and API-key redaction. | No | [api_docs_browser.cjs](../../tests/javascript/api_docs_browser.cjs) |
| `api_keys` | API-key creation, one-time secret display, lifecycle actions and redaction. | No | [api_keys_browser.cjs](../../tests/javascript/api_keys_browser.cjs) |
| `audit_export` | Audit exports, downloaded evidence and authorization boundaries. | No | [audit_export_browser.cjs](../../tests/javascript/audit_export_browser.cjs) |
| `audit_history` | Audit-history filters, entries, responsive layout and light/dark presentation. | No | [audit_history_browser.cjs](../../tests/javascript/audit_history_browser.cjs) |
| `audit_minutes` | Audit-minute review and export journeys, filtering and permission behavior. | No | [audit_minutes_browser.cjs](../../tests/javascript/audit_minutes_browser.cjs) |
| `concept` | Current and draft Concept records, actions, responsive layout and accessibility. | No | [concept_browser.cjs](../../tests/javascript/concept_browser.cjs) |
| `concept_discard` | Draft discard confirmation, cancellation, success feedback and focus restoration. | No | [concept_discard_browser.cjs](../../tests/javascript/concept_discard_browser.cjs) |
| `dataset_creation` | Creation eligibility, saved ownership and reader/maintainer access. | Yes | [dataset_creation_browser.cjs](../../tests/javascript/dataset_creation_browser.cjs) |
| `dataset_deletion` | Deletion confirmation, stale-state rejection, focus restoration and data integrity. | Yes | [dataset_deletion_browser.cjs](../../tests/javascript/dataset_deletion_browser.cjs) |
| `dataset_diff` | Draft review summaries, field and record diffs, reordering and publication. | No | [dataset_diff_browser.cjs](../../tests/javascript/dataset_diff_browser.cjs) |
| `dataset_versions` | Draft publication, cancellation, version snapshots, imports and reader visibility for private/future publications. | No | [dataset_versions_browser.cjs](../../tests/javascript/dataset_versions_browser.cjs) |
| `field_datatypes` | Supported field types, identifier constraints, validation and typed records. | No | [field_datatypes_browser.cjs](../../tests/javascript/field_datatypes_browser.cjs) |
| `mapping` | Mapping-table layout, source updates, record interactions and field reordering. | No | [mapping_browser.cjs](../../tests/javascript/mapping_browser.cjs) |
| `mapping_usage` | Mapping usage summaries, source and target navigation and responsive presentation. | No | [mapping_usage_browser.cjs](../../tests/javascript/mapping_usage_browser.cjs) |
| `navigation` | Responsive navigation, keyboard use, focus management and page transitions. | No | [navigation_browser.cjs](../../tests/javascript/navigation_browser.cjs) |
| `navigation_tabs` | Dataset tab navigation, responsive behavior and keyboard focus. | No | [navigation_tabs_browser.cjs](../../tests/javascript/navigation_tabs_browser.cjs) |
| `new_version` | Manual draft changes, CSV import review, confirmation and publication. | Yes | [new_version_browser.cjs](../../tests/javascript/new_version_browser.cjs) |
| `overview` | Overview sections, filtering, responsive cards, accessibility and contrast. | No | [overview_browser.cjs](../../tests/javascript/overview_browser.cjs) |
| `record_search` | Record search, status filters, pagination, browser history and responsive controls. | No¹ | [record_search_browser.cjs](../../tests/javascript/record_search_browser.cjs) |
| `ref165_regressions` | Export-access workflows, metadata presentation and failed-request recovery. | No | [ref165_regressions_browser.cjs](../../tests/javascript/ref165_regressions_browser.cjs) |
| `ref259_headers` | Security and cache headers on browser responses and static assets. | No | [ref259_headers_browser.cjs](../../tests/javascript/ref259_headers_browser.cjs) |
| `rest_api` | REST login, records endpoints, CSRF, caching, authorization and key revocation. | Yes | [rest_api_browser.cjs](../../tests/javascript/rest_api_browser.cjs) |
| `select` | Enhanced-select keyboard navigation, validation, focus transitions and CSP. | No | [select_browser.cjs](../../tests/javascript/select_browser.cjs) |
| `source_formats` | Imported formats, localized numeric/date previews and validation responses. | No | [source_formats_browser.cjs](../../tests/javascript/source_formats_browser.cjs) |
| `source_verdicts` | Source-format verdicts, keyboard focus and preview/confirmation values. | No | [source_verdicts_browser.cjs](../../tests/javascript/source_verdicts_browser.cjs) |
| `table_rows` | Table semantics, selection, keyboard focus, side panels and responsive layout. | No | [table_rows_browser.cjs](../../tests/javascript/table_rows_browser.cjs) |
| `ui_polish` | Dataset tables, tabs, draft actions and component presentation at key widths. | No | [ui_polish_browser.cjs](../../tests/javascript/ui_polish_browser.cjs) |
| `ui_refresh` | Refreshed table sizing, text wrapping, row focus and responsive behavior. | No | [ui_refresh_browser.cjs](../../tests/javascript/ui_refresh_browser.cjs) |
| `view_transitions` | Navigation history, scroll restoration, reduced motion and focus. | Yes | [view_transitions_browser.cjs](../../tests/javascript/view_transitions_browser.cjs) |
| `web_csp` | Browser interactions and assets while the production CSP is enforced. | Yes | [web_csp_browser.cjs](../../tests/javascript/web_csp_browser.cjs) |

¹ `record_search` returns to the PR core after REF-315 is fixed, in that issue's PR.

## Running tests locally

Install the development dependencies and the locked JavaScript tools, then install Chromium once:

```shell
python -m pip install -e ".[dev]"
npm ci --ignore-scripts --prefix tests/javascript
node tests/javascript/node_modules/playwright/cli.js install chromium
```

Run the pull-request core set, the full suite, or named cases:

```shell
python tests/run_browser_tests.py --core
python tests/run_browser_tests.py --all
python tests/run_browser_tests.py dataset_creation record_search
```

The runner starts and destroys an isolated fixture for every case. Browser scripts refuse non-loopback targets. Chromium is the supported browser for this suite; cross-browser coverage is not part of REF-310.

## CI policy

Every pull request runs the short core set. It protects CSP and navigation, the critical dataset lifecycle, access management, and the REF-294 REST installation policy. The record-search case joins this set after the responsive defect tracked in REF-315 is resolved.

The full suite can be started manually and is scheduled daily at 02:17 Europe/Amsterdam. A scheduled run skips the expensive browser job when `main` has the same commit as the previous completed scheduled run. Manual runs always execute.

An acceptance script must remain red when it exposes a confirmed product defect. Record that defect as a linked Jira bug instead of weakening the assertion or folding an unrelated product fix into a browser-maintenance change.
