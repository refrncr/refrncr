# Administrator audit history (REF-39)

[REF-39](https://cleverhorizons.atlassian.net/browse/REF-39) adds **Settings → Audit log**
for active Community administrators. It reads all retained successful domain events from
the existing REF-149 catalog and `audit_events` table. Access is installation-wide;
Dataset grants and team membership confer no audit access. This is Community functionality,
without Cloud tenant composition. [REF-31](https://cleverhorizons.atlassian.net/browse/REF-31)
continues to own security-event logging. The [audit policy](audit-policy.md) remains authoritative
for production, safe payloads and transaction boundaries.

## Read boundary and authorization

`AuditHistoryService.list_history` and `get_event` independently require a current, active,
non-deleted administrator through the existing Unit of Work user port. Both list and detail
routes also use the existing session and administrator helpers. Authorization precedes filter,
cursor and event lookup. A supplied actor/object/event ID never grants access. Anonymous and
inactive sessions follow the existing safe login flow; ordinary authenticated users receive 403
irrespective of whether an event exists. An authorized missing detail receives 404.

`AuditReadOperations` in `ports.py` describes bounded page/detail projections and name batches.
The SQLAlchemy implementation belongs in `persistence.py`; neither the service nor the web route
imports Flask/database machinery across this new boundary. Database read failures are translated
to the neutral `AuditReadUnavailable` exception. Templates receive `AuditView` values, never ORM
relationships or arbitrary context dictionaries. Composition follows ADR-009 and ADR-010;
no new architecture, dependency, public business API or second event model is introduced.

The HTTP endpoints are `GET /settings/audit-log` and `GET /settings/audit-log/<event_id>`.
Responses use `Cache-Control: no-store` and same-origin referrers. They share the existing login,
cookie, CSRF and non-disclosure conventions. GET/HEAD reads never flush/commit domain changes or
emit audit events. The read port has no edit/delete/redact/restore operations. No cascade or
retention behavior changes. Existing application append-only behavior is preserved; this feature
does not add database-enforced immutability, cryptographic tamper evidence or an immutable ledger.

## Filters and time

GET parameters `start`, `end`, `actor`, `action`, `object_type` combine with AND. Blank filter
values mean unselected. Unknown or duplicate parameters (even equal duplicates), malformed IDs,
unknown catalog selections, oversized URLs/values and reversed date ranges fail with an accessible
400 error. Invalid input never silently broadens a result. `cursor` is optional but must be
non-empty when supplied. Clear filters returns the unfiltered first page. Browser refresh,
back/forward and pagination preserve the URL state.

Dates require ISO date/time and an explicit `Z` or numeric offset. The server normalizes to UTC;
start is **inclusive**, end **exclusive**. Equal bounds are valid and produce no results.
The actor filter uses a canonical stable UUID, including deleted actors. The bounded actor selector
offers actors on the current page plus the selected actor. Every detail offers **Filter by this
actor**, including a deleted actor. This avoids loading an unbounded installation-wide user list;
it is not a free-text directory search. Action selections identify `(object_type, action)` pairs,
because historical names such as `created` belong to several contracts.

Every timestamp has an ISO UTC `<time datetime>` and a full UTC fallback with six fractional
digits. JavaScript uses the browser timezone and preserves all six displayed fractional digits.
Details display both local and exact UTC timestamps. Invalid/unsupported localization preserves
the server fallback. No locale-dependent date parsing is used.

JavaScript enhances date filters to native local date/time controls. Native controls support
milliseconds: unchanged fields retain the original full-precision UTC value, including the second
occurrence of a repeated daylight-saving hour. Editing a field selects a new instant at the
control's precision. Newly entered repeated hours use the first occurrence; nonexistent local
times during the spring transition produce a focused error. Without JavaScript or usable `Intl`
timezone support, date controls remain explicit-offset text inputs. Invalid calendar timestamps
are not silently normalized to another day. Without JavaScript, explicit ISO UTC
or offset input and direct full-page details remain available.

## Cursor and query model

The order is descending `(timestamp, id)` with 50 visible events and a 51-row lookahead. Queries
use keyset predicates and no offset navigation. Older/newer links bind the normalized filters,
administrator, direction, boundary and first-page maximum position. The versioned cursor is an
opaque application token authenticated with HMAC-SHA256. It expires 12 hours after the initial
page; subsequent links retain that issuance time. Bad signatures, expiry, inconsistent filters,
another administrator or invalid positions fail closed.

The service accepts an injected signing key of at least 32 bytes. Default Community composition
creates one random key per service instance: restarting it invalidates outstanding links safely.
A deployment composing multiple worker instances must inject the same securely managed key into
each `AuditHistoryService`; a cursor is not portable across independently generated keys. No key
is persisted in audit data, URLs or source control.

The first-page maximum excludes newer chronology positions during navigation. Inserts ahead of
the current boundary cannot skip or duplicate previously retained rows. This is keyset navigation,
not an MVCC snapshot kept alive across HTTP requests: a late-committing/backdated event below the
maximum can become visible on a later page. No total count, large offset, unbounded event load,
reconstructed historical name or polling query is used.

Precisely: a forward traversal preserves every originally retained matching event exactly once,
assuming existing append-only rows remain unchanged. An insert behind an already passed boundary
is not retroactively included. An insert below the maximum and ahead of the next boundary can
appear; reverse navigation after such an insert can have different page membership. Refresh
starts a new traversal. REF-39 acceptance criterion 28 records this guarantee, not frozen pages.

A list read performs at most seven SQL queries: authorization, one page, one actor batch and up
to four object-name batches. Each resolution query is bounded to 50 objects (51 actors when the
selected filter is not on the page). Details need at most four queries. List projections omit
context except for the bounded `dataset.deleted` name snapshot. Detail queries bound JSON text
to 4096 characters before application parsing;
oversized/invalid historic payloads produce no additional details. These are bounded result and
render sizes, not a promise of constant database scan cost for every sparse filter.

Migration `0013_ref_39` adds only `ix_audit_events_timestamp_id(timestamp, id)`. On 10,000 rows,
SQLite changes from scan plus temporary order B-tree to indexed keyset search; PostgreSQL changes
from sequential scan plus sort to backward index scan. No engine-specific hints are used.
Upgrade/downgrade tests preserve historical rows and context exactly. Index creation is ordinary
transactional DDL, not an online/concurrent migration; operators should account for index build
time/locking on large installations. Optional filter indexes should follow measured workloads.

SQLite's naive stored datetimes are interpreted as UTC; PostgreSQL values are normalized to UTC.
Both engines use the same ordering, boundaries, lookahead, batch projections and context validation.

## Presentation and privacy

`audit_presentation.py` supplies explicit English labels for all 30 catalog pairs and eight object
types. Context keys/types come from `audit.EVENTS`; they are not copied into a competing contract
catalog. Only approved structural context validated by `validate_event` is shown as labelled fields.
Unknown extra keys are dropped, invalid approved values suppress context, and unknown historical
contracts show neutral unavailable labels. Raw JSON is never rendered. Ten current context labels
cover UUID, bounded count, subject type, import/export format and the REF-141 bounded
Dataset-name snapshot.

`ACTION_PRESENTATIONS` attaches a required explicit `deletes_object` decision to every existing
action label; `ACTION_LABELS` is derived from it. This is presentation metadata, not another event
catalog. A validated catalog pair with delete semantics takes precedence over current-name lookup:
its committed event itself establishes deletion, even for an unnamed object. No suffix heuristic
or speculative Dataset-delete contract is used. The eight current decisions are:

| Object type | Technical action establishing deletion |
| --- | --- |
| `dataset` | `dataset.deleted` (permanent deletion; bounded name snapshot) |
| `user` | `deleted` (soft deletion) |
| `team` | `deleted` |
| `membership` | `deleted` |
| `grant` | `deleted` |
| `dataset_field` | `deleted` |
| `dataset_record` | `deleted` |
| `dataset_export_access` | `revoked` (removes the access row) |

These events show **Deleted object** in both list and detail. REF-141 shows
**name (deleted)** when its approved snapshot is available. Neither requires an object-name query.
In the list's Object column, unnamed/deleted fallback labels are italic; resolved names remain
normal. This uses the central object status, not a comparison with the label text. Detail labels
retain their normal style.
The Dataset action is exactly `dataset.deleted`, not the historical unprefixed `deleted`.
For **non-delete events**, apply this table:

| Object type | Current name resolver | Object presentation without a name |
| --- | --- | --- |
| `user` | Non-deleted `User.display_name` | Deleted object only after confirmed absence/soft deletion; otherwise No name to display |
| `team` | `Team.name` | Deleted object only after confirmed absence; otherwise No name to display |
| `dataset` | `Dataset.name` | Deleted object only after confirmed absence; otherwise No name to display |
| `dataset_field` | `DatasetField.name` | Deleted object only after confirmed absence; otherwise No name to display |
| `membership` | None; stable ID in detail | No name to display |
| `grant` | None; stable ID in detail | No name to display |
| `dataset_export_access` | None; stable ID in detail | No name to display |
| `dataset_record` | None; never Record values or identifier values | No name to display |

For non-delete events, the read port's `resolve_objects` returns one `AuditObjectRecord(exists, name)` per requested ID
for each supported named type. A completed query establishes absence; an empty/unsafe name does
not. The central presentation layer derives explicit `resolved`, `unnamed` or `deleted` status
and its label for both list and detail. Unknown types and invalid IDs have no authoritative
existence lookup and use **No name to display**, without claiming existence or deletion.
Missing resolver support, incomplete results and unexpected resolver/database failures never
become object statuses: they produce the existing neutral 503 response and the fixed technical
diagnostic `Audit object resolution failed`, without IDs, names, SQL or exception payloads.
No existence queries are added for intentionally unnamed types, and batching limits are unchanged.

Historical per-Record audit rows remain append-only. A successful new non-empty bulkimport
produces exactly one `dataset:dataset.records_imported` summary, while manually creating one
Record still produces one `dataset_record:created`. No import production code is changed by
this presentation correction; no Record contents or historical name snapshots are reconstructed.

Actors use current non-deleted `User.display_name`, otherwise **Deleted user**. Active/inactive
status is not displayed; email is never fetched for label resolution. REF-149 permits no system
or anonymous actor; bootstrap uses the initial administrator's actual ID. There is intentionally
no synthetic system actor fallback. Unknown types do not resolve other tables speculatively.

Labels are bounded and controls/bidirectional formatting characters are removed in presentation
only. Jinja escapes text and `<bdi>` isolates names. Stored names/events are not rewritten.
Renaming a current object changes its visible label, not the historical event. Dataset descriptions,
Record/upload/export values, credentials, session/CSRF tokens, local paths, SQL and traces are never
used as context or fallback labels. Stable actor/object IDs appear in details and filter/navigation
mechanics, not as table display names.

The semantic table reuses Settings, table, button and form styles. Read-only details reuse the
shared side panel, including loading, native dialog semantics, focus trap, Escape, close controls
and exact opener focus restoration. Detail links include the event ID in their accessible name,
so equal action/timestamp pairs remain distinguishable. Shared heading normalization preserves initial focus. Queued
close events cannot discard a newer panel load. A guarded local-time initializer also handles
fetched content. Light mode uses existing tokens; dark mode is explicitly outside this delivery
at the product owner's request. No visual token or framework was added.

## Extending coverage

1. Change event production/allowlists only through REF-149's catalog and audit-policy workflow.
2. Add an explicit action label and, for a new object type, an `ObjectPresentation` entry.
3. Decide whether a bounded current-name-only resolver is safe; otherwise declare no resolver.
   Every action also requires an explicit delete/non-delete decision in `ActionPresentation`;
   missing decisions, including label-only additions, fail the guard. A named resolver must implement the port's explicit per-ID existence result and be supported
   by the adapter and completeness guard. Absence of a resolver/name is not evidence of deletion.
4. Add context labels and a supported validator/formatter for every newly approved context kind.
   Do not render arbitrary dictionaries or invent historical content snapshots.
5. Extend completeness, privacy, authorization, query-bound and SQLite/PostgreSQL tests.
6. Update the operation inventory/matrix and run the existing audit guard plus required checks.

`require_complete_presentation` fails clearly when an action, object type, context label or safe
formatter is missing, rejects stale action/type entries and cross-type resolvers, and permits
coherent catalog extensions. Runtime resolution also refuses cross-type metadata. Unrelated
non-audit UI labels are outside this guard. There are still exactly 29 event contracts; the two read operations and
two HTTP routes are explicitly inventoried as intentionally excluded from domain audit.

## Verification and exclusions

`test_audit_history.py` covers boundaries 0/1/49/50/51/100/101, 127 and 10,000 events, equal timestamps,
inserts, reverse navigation, filters, cursor tampering/expiry, authorization, current/deleted names,
privacy, query counts, database failure translation and real query plans. Migration tests cover
both engines. `test_audit_history_review.py` adds simultaneous writer/read coordination, backdated
insert semantics, demotion/session forgery, every resolver type, hostile filters, guard sabotage
and read-stage failures with no commits. `test_audit_time_javascript.py` covers six timezones, date boundaries, both DST
transitions, invalid/unsupported timestamps and duplicate registration. Existing pane tests remain
part of pytest. The optional browser fixture/script tests native controls/dialog behavior:

```sh
.venv/bin/python tests/browser_audit_history_app.py
node tests/javascript/audit_history_browser.cjs
```

Set `REFRNCR_PLAYWRIGHT_MODULE` and `REFRNCR_BROWSER_EXECUTABLE` to your existing external Playwright
module and Chrome executable. Screenshots use `REFRNCR_SCREENSHOT_DIR` or the OS temporary directory.
The fixture is disposable, in-memory and loopback-only. Playwright remains optional external test
tooling, not a new application or CI dependency. See the [part-1 report](ref-39-part-1-report.md)
and the [independent review report](ref-39-part-2-report.md) for executed commands/results and remaining limits.
GitHub Actions is authoritative before merge.

Out of scope: new events/payloads, auditor role, team/object audit pages, audit export/API, free text,
context search, retention configuration, event mutation, before/after Record values, security logging,
Cloud tenancy, SIEM, live updates and polling.
