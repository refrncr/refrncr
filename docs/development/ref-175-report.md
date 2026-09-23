# REF-175 implementation report

## Scope and design decisions

REF-175 refines existing Community/core Dataset, Record and API-key interfaces without adding a
new framework, dependency or persistence model. The work uses the existing design tokens,
badges, compact controls, tables, modal side panels and server-rendered validation contracts.

- API keys remain under **Access management**, after Teams. The revoke confirmation uses the
  shared spacing scale, and the Identifier column has a compact fixed width with an ellipsis.
  Its complete value remains available through the focusable control's accessible label and
  native title.
- Dataset metadata represents the version as `V<number>` and keeps Current as a separate pill.
  Status pills no longer include a decorative dot. Dataset-list concepts use the shorter
  **Concept** label and expose **Concept in progress** as the link title.
- Published Record tables omit the concept-only System status column. Record-table headers stay
  visible inside the table's own vertical and horizontal scroll region, preserving header/body
  alignment at narrower supported widths.
- Concept Record changes use three independent **Added**, **Edited** and **Deleted** toggle
  buttons. No selection shows all Records; multiple selections use inclusive OR semantics.
  Counts follow the current search term and are calculated before applying the change filters.
- Add/Edit Record validation responses remain in the modal side panel. Submitted values are
  retained, field errors are connected with `aria-invalid` and `aria-describedby`, and a duplicate
  unique value identifies and focuses the conflicting field. Database constraints remain the
  final concurrency safeguard.

## Component consistency

No new design tokens were introduced. Existing spacing, compact-control height, status colours,
badge, table-scroll and side-panel primitives were reused. The deliberate page/component rules
are the compact API-key Identifier column, the Record-table sticky header, concept change-filter
toggles, published-version column omission and Record-form field-error rendering.

The Record form continues to have a standalone progressive-enhancement fallback. Its response
also implements the existing `data-side-panel-content` contract, while inline Record panels now
provide the panel host required by the shared asynchronous submit controller. Keyboard focus
moves to the invalid field and full error text remains available to assistive technology.

## Impact review

- Authorization, tenant isolation, CSRF, audit and transaction boundaries: unchanged.
- Database schema and migrations: unchanged.
- Public API contracts: unchanged.
- Security: server-side validation and unique database constraints remain authoritative; escaped
  submitted values are redisplayed only to the authenticated editor who submitted them.
- Dependencies and architecture: unchanged; no ADR is required.
- Documentation: this report records the final REF-175 behaviour and component decisions.

## Verification

| Check | Result |
| --- | --- |
| Focused Record form, side-panel and Dataset browser regression selection | 178 passed, 2 skipped. |
| Final complete test suite | 2,724 passed, 1,175 skipped, 3 existing warnings. |
| `.venv/bin/python -m pre_commit run --all-files` | Passed all configured hooks. |
| `.venv/bin/python -m ruff check .` | Passed. |
| `git diff --check` | Passed. |

The complete suite was run outside the filesystem/network sandbox so the configured PostgreSQL
connection and packaged-CLI loopback-port tests could execute. GitHub Actions remains the
authoritative independent pre-merge quality gate.
