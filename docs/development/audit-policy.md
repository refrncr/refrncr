# Audit coverage and contracts (REF-149)

[REF-149](https://cleverhorizons.atlassian.net/browse/REF-149) owns event production and
coverage for the current Community/core installation. The
[coverage matrix](audit-coverage.json) is the authoritative current audit decision for every
operation. The [functional inventory](audit-inventory.json) maps HTTP methods/routes, CLI commands,
module commands and programmatic services to those operations. The matrix has one row per service
operation regardless of the number of interfaces; its event references distinguish alternatives
and composite writes. Read these JSON artifacts as maintained documentation, not generated claims
that runtime behavior must necessarily be correct.

The framework-independent [catalog](../../src/refrncr/audit.py) validates event contracts and
explicit operation classifications. It uses the existing `AuditEvent`, `AuditEntry` and Unit of
Work; there is no second event model, logging framework, repository, event bus or external service.
The static [guard](../../src/refrncr/audit_guard.py) verifies the inventory/matrix/catalog agreement.

## Decisions and scope

The current inventory contains 82 public application-service operations, plus 36 security,
transient, technical and administrative operations. Its 118 decisions are 32 domain-audited,
19 security logging required, 11 technical logging only and 56 intentionally excluded. There are
47 explicitly declared HTTP routes (46 Flask and one health route), six CLI flows including the
opt-in `python -m refrncr.argon2_benchmark`, and documented implicit framework endpoints. The
catalog supports 30 `(object_type, action)` contracts. REF-39 adds two intentionally excluded reads;
the guard compares symbols rather than freezing these counts.

Domain audit records committed business changes and prepared authorized Dataset export requests.
Ordinary Dataset listing, viewing, page navigation, policy checks, preview, staged-import navigation
and cancellation create no successful domain event. Authorization/validation/stale/rollback failures
never masquerade as successful changes. Retention cleanup, startup, singleton bootstrap and operator
migration/preflight are technical operations with explicit exclusion reasons. The benchmark uses
synthetic credentials and is technical only. No authenticated business API or background business
worker exists; health, framework schema/docs, static assets, HEAD and OPTIONS are inventoried.

`security_logging: true` in the matrix means a security obligation, including the operation's denied
or suspicious flows. It does **not** mean REF-149 implemented security logging or that each successful
ordinary read should be logged. REF-31 remains the owner. Technical errors remain diagnostics;
audit records never contain exceptions, raw SQL or traces.

Community remains installation-scoped, with no tenant/organisation columns or Cloud composition.
Cloud tenant-specific audit isolation and SIEM/log shipping belong to `refrncr-cloud`.
[REF-39](https://cleverhorizons.atlassian.net/browse/REF-39) adds administrator-only
[audit-history presentation](audit-history.md), without before/after Record contents or Dataset
descriptions. It preserves REF-149's contracts and introduces no retention infrastructure,
tamper-evident ledger or audit API.

Audit object presentation distinguishes **No name to display** (no safe name/resolver) from
**Deleted object** (a successful committed catalogued delete-event, or later absence established
by a supported named-object resolver). Explicit per-action presentation metadata covers the six
`deleted` contracts and `dataset_export_access:revoked`; REF-141 adds `dataset:dataset.deleted` with a bounded name snapshot.
Delete evidence takes precedence over name resolution. Resolver failures
remain safe technical errors, never deletion evidence. Record values and identifiers are not names;
historical per-Record events remain untouched. See the REF-39 guide for per-type decisions.

## Event names, actors and safe context

Historical actions such as `created`, `deleted`, `metadata_updated`, `business_key_defined` and
`owner_changed` retain their spelling and object types. Their identity is the pair
`(object_type, action)`. New actions use a deliberate lower-case capability prefix:
`dataset.export_requested` and `dataset.records_imported`, both with object type `dataset`.
Historical per-Record import `created` rows are retained unchanged. New confirmed imports emit only
the summary contract; this is a production behavior correction, not a rewrite of historical events.

Services authenticate/authorize their supplied actor using the established boundaries. Browser
handlers supply `actor.id` from the resolved server-side session, never a form/query actor. Trusted
programmatic callers must likewise establish a principal before invoking services. Initial setup
is the explicit exception to pre-existing authentication: the newly created first administrator
is its own actor under the serialized initial-setup contract. No anonymous or invented system actor
is accepted; technical setup/migrations have no domain event.

`record_audit` validates before calling the persistence port, so alternate adapters receive the same
contract. The catalog requires canonical UUID actor and object identifiers, a supported object/action
pair and exactly the declared context fields. It copies context and rejects additional/missing fields
without including rejected values in errors. Existing minimal events need no details beyond their
stable target and actor.

Currently stored structural context is restricted to:

- canonical UUIDs for owning teams, creator, Dataset and export-access subject;
- subject enum `user` or `team`;
- canonical format `CSV`, `Excel`, or `JSON` (import permits CSV/Excel only);
- integer import counts between 0 and 10,000, matching the parser limit;
- only for `dataset.deleted`, a Dataset-name snapshot of at most 200 codepoints.
  The central validator bounds before processing, strips controls/direction overrides
  and substitutes a neutral label if nothing displayable remains. Confirmation itself
  still compares the unmodified stored name exactly.

Enums are limited to 16 input characters before NFC/whitespace/case normalization and then allowlisted.
UUIDs are exactly 36 canonical characters; counts reject booleans, strings, floats and out-of-range
values. No other arbitrary text field or list is approved. A future field-key context requires
an explicit bounded validator and contract/test change; merely putting it in a dictionary is not enough.

Except for the explicit REF-141 Dataset-name snapshot, never include names/descriptions as
business content, old/new Record values, uploads, downloads,
filenames, local paths, passwords, API keys, credentials, session/CSRF tokens, SQL, stack traces or
unbounded request data. The JSON column already supports scalar counts, so only its Python annotation
changes; no new migration/schema/dependency is needed. Historical ORM reads are not retroactively
validated against current production contracts.

## Transactions, no-ops and retries

Mutation, cleanup, business timestamps and audit insertion share one write Unit of Work. Failure
before/after audit flush or before commit rolls back the whole operation. Operation groups never
commit themselves. Audit validation errors fail the mutation as well.

Normalized e-mail, team rename, permission change, Dataset metadata, field, identifier, order and
Record no-ops retain their persisted timestamps and create no event. Record no-ops also retain their
revision. Already enabled/disabled/deleted user state creates no extra event. Services still reject
invalid lifecycle operations, including enabling a deleted user. Row/installation/Dataset locks
protect the state comparisons; a repeated concurrent e-mail change produces only one committed change.

Expected Record revisions, owning-team IDs and field order are checked before accepting a same-value
no-op. No general metadata revision is invented. Existing duplicate constraints, conditional writes,
missing-target errors and import claims retain their replay contracts. A retry after a rolled-back
import may proceed after the transient claim is released; a consumed successful claim cannot execute
again. Repeating a distinct valid export request is a new extraction request and is audited again.

Combined operations retain their explicit historical contracts:

- Dataset edit emits `metadata_updated` and/or `owner_changed` only for the parts that changed.
- Creating an identifier field emits field `created` plus Dataset `business_key_defined` or
  `business_key_updated`. Combined field edit emits definition and/or identifier changes as needed.
- `move_field` delegates to `replace_order` and adds no wrapper event.
- Deleting one team retains one team `deleted` and the existing per-Grant `deleted` events.
  Membership/export-access cascades belong to that team's lifecycle event. This is a single-object
  operation with historical cleanup semantics, not a confirmed bulk selection. No new per-membership
  or per-export-entry cascade events are invented.

## Bulk import

A confirmed, non-empty CSV/Excel import emits exactly one `dataset.records_imported` event on the
Dataset. Actor, Dataset UUID and timestamp use the normal event fields. Details contain format,
committed `record_count` and `rejected_row_count`. Current imports are all-or-nothing, so successful
rejected count is authoritatively zero. There is no event for failed validation, stale schema,
authorization failure, cancellation, replay or rollback, and no per-Record `created` event on this path.
Manual Record creation retains its existing event. Nothing from the staged filename, worksheet,
mapping, upload, receipt or Record payload enters audit context.

## Dataset export request semantics

`DatasetRecordService.export_dataset` validates format, opens the existing coherent read snapshot,
resolves authorization and prepares the complete bounded-memory spool. Readability preflight now
belongs to the service and runs inside that snapshot. The service closes the read UoW, then commits
one audit-only write UoW before returning the artifact to Flask or a future caller. See
[ADR-012](../architecture/adr-012-audit-export-boundary.md) for the approved transaction boundary.

Preparation or audit-insertion/commit failure closes the artifact and returns no download and no
successful request event. The event means a valid authorized export was prepared, not that response
construction, transport or full client receipt succeeded. Post-return transport failure retains the
accurate event, emits the fixed technical diagnostic `Dataset export stream failed`, and produces no
second domain event. No exception payload is attached to that diagnostic. Exceptions observed during stream iteration or
cleanup produce that diagnostic once. Socket failures outside the iterator remain the serving stack's
diagnostics; the application cannot infer full delivery or reliably detect every disconnect.
Generator closure releases
the underlying stream. A response-construction failure similarly retains the accurate prepared-request
fact while existing error handling releases the spool.

Authentication, content and authority remain pinned to the snapshot. A revoke after it starts affects
subsequent exports, even if audit insertion happens after that revoke. PostgreSQL repeatable-read and
explicit SQLite read transactions remain unchanged; no writer is held during artifact generation.
Flask HEAD executes the GET export handler, so a prepared authorized HEAD request has the same one-event
semantics. Automatic OPTIONS only describes routing and creates no event.

## REF-31 handoff

[REF-31](https://cleverhorizons.atlassian.net/browse/REF-31) implements centralized structured security
logging. Each relevant matrix row contains the required handoff. Required categories include:

- login failure (including unknown/inactive/deleted user), invalid/expired/revoked session,
  throttling/capacity rejection, malformed proxy input and insecure transport;
- missing, duplicate or invalid CSRF, oversized/unsupported uploads, forged subjects, cross-Dataset
  references and unauthorized operations; responses remain non-disclosing;
- permission/ownership/export-access and membership changes, account lifecycle, and import/export
  outcomes using safe identities, format/counts and reason codes;
- suspicious stale/replayed/tampered import claims or revisions, without recording tokens, payloads
  or user-supplied filenames; ordinary concurrency is not automatically an attack.

REF-31 must distinguish successful security-relevant outcomes from denials. Technical failure logs
must not copy audit context blindly or put exception data in domain audit. API-key and publication
security categories remain future functionality; they have no current service implementation.

## Maintaining coverage and CI

1. Add the operation to the existing appropriate application service, or explicitly register a new
   framework-independent service. Interfaces reuse it and supply trusted actor context.
2. Add its explicit `Operation` registration in `audit.py`, including an exclusion reason when no
   domain event is needed. Add an `EventContract` only for a new supported object/action pair.
3. Route event construction through `record_audit` in the service, within its existing UoW.
4. Update `audit-inventory.json` with the service symbol and every interface mapping. Keep delegated
   operations and composite conditional events clear; no line numbers or duplicate business entries.
5. Update `audit-coverage.json` with every required decision, context, failure/no-op/rollback behavior,
   security handoff and actual test symbols. Add behavior cases for the operation, including denied,
   validation, duplicate, rollback and database-relevant concurrency paths.
6. Run `.venv/bin/python -m pytest tests/test_audit_catalog.py tests/test_audit_coverage.py` with
   `REFRNCR_TEST_POSTGRES_URL` set to an isolated test database. Unset PostgreSQL skips are not
   PostgreSQL validation. Run the relevant existing tests and Ruff as well.

The existing GitHub Actions `python -m pytest` step automatically includes the guard; no new tool or
workflow job is required. It independently discovers every public `*Service` method and classes
injecting `self.uow`, plus explicitly identified supporting classes. This includes reads, so it does
not guess whether names such as `get` or `perform` mutate. It also checks HTTP paths/methods, argparse
subcommands, module CLIs, supported events, nonempty exclusions, required matrix columns and real test
symbols. The guard checks exact operation-set agreement, typed audit/security decisions and both
directions of interface-to-matrix references. A separate test traces the current Flask service
receiver aliases through shared local helpers; alias changes require review. Tests inject a new
service and corrupt inventory/matrix fields to prove that CI identifies missing or inconsistent
decisions. Direct
`uow.add_audit`/`AuditEvent` construction in current services/web is rejected as a catalog bypass.

The guard is a completeness tripwire, not proof that arbitrary Python code performs no hidden writes.
A newly introduced adapter-driven top-level workflow or dynamic interface mechanism must be registered
and added to discovery during review. Do not move a use case into an unregistered helper to bypass it.
Runtime behavior tests provide evidence that the declared decisions are implemented. Part-2 review and full local SQLite/PostgreSQL 16 validation are recorded in the
[final report](ref-149-part-2-report.md). Local checks do not replace GitHub CI before merge.


## Optional real-browser verification

Start a fresh disposable fixture with `.venv/bin/python tests/browser_table_rows_app.py`, then run
`node tests/javascript/audit_export_browser.cjs` using the existing external Playwright convention
(`REFRNCR_PLAYWRIGHT_MODULE` and `REFRNCR_BROWSER_EXECUTABLE`). It checks actual CSV/Excel/JSON downloads
and denial of a direct export URL for an ordinary reader. The existing `export_access_browser.cjs`
and `dataset_governance_browser.cjs` cover access changes, duplicates, revocation and stale forms;
use a fresh fixture for each script. These optional checks add no dependency or CI requirement.
Persisted audit counts, rollback and safe context are asserted by the SQLite/PostgreSQL service and
Flask tests, not inferred from a successful download.

## Dataset deletion (REF-141)

`DatasetService.delete_dataset` authorizes the current Dataset administrator and compares
the current name exactly, with the expected UTC metadata timestamp checked under its lock.
It deletes the [inventoried operational dependencies](ref-141-report.md), preserving all
historical events, then writes exactly one `dataset.deleted` on the Dataset UUID in the same
Unit of Work. The only context key is `dataset_name`; counts are returned to the caller but
are not persisted because they are unnecessary for this deletion contract. There are no
per-child events. Denied, mismatched, stale, repeated and rolled-back requests produce no
delete event. SQL constraints, audit flush and commit failures roll back the complete write.

The production name validator is shared by audit list/detail presentation. Both show the
snapshot with an explicit deleted status; neither resolves or links a current Dataset.
Existing historical events remain unchanged and retain their existing fallback rules.
