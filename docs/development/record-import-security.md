# Record import security

The REF-160/259 sections below define current product behavior: one complete-file Import flow
for empty and populated tables. Earlier REF-81/134/149 descriptions document the retained
create-only service contract where they differ (append persistence and `dataset.records_imported`
audit). Product imports use a four-step wizard, paged compact preview, results redirect and
`dataset.new_version_imported` summary.

Community Record imports stage at most 10 MiB in a bounded, process-local transient store. An
opaque token binds the bytes, mapping, worksheet, actor, login session, and Dataset for 15 minutes.
Logout removes session-bound imports; confirmation claims the token before reparsing and uses one
serialized Dataset transaction. Successful confirmation leaves a non-executable completion receipt
until expiry. Staged content and wizard state are not persisted. Successful confirmation persists
the structural audit summary described below; it contains no upload or Record values.

Under REF-73, import requires the central Dataset maintenance capability: active owning-team
membership or Community administrator access. Confirmation rechecks current authority inside the
same Dataset-locked transaction as persistence. A staged preview grants no continuing rights after
membership removal or ownership reassignment. Historical Dataset Grants and export access do not
authorize imports.

CSV parsing uses Python's standard library. Modern `.xlsx` parsing uses `openpyxl` (MIT licensed) in
read-only mode with formulas left unevaluated. A ZIP preflight bounds entry count, expanded size,
and compression ratio and rejects macros, external links, and external data connections. Only
visible worksheets can be selected and mapped formula cells are rejected.

The transient store is deliberately process-local because the supported Community server profile
uses one worker. Multi-worker deployments require sticky routing for the complete import flow;
without it, a later step safely reports the token as expired or unavailable and requires a new
upload. Process-local tokens can never resolve to another process's import, and no writes begin
until confirmation resolves and claims the token in the receiving process.

## Mapping assistance

Mapping assistance is deterministic and local. Comparison trims whitespace, applies Unicode NFC
normalization and case folding, optionally removes diacritics for comparison only, splits common
camelCase boundaries, and treats underscores, hyphens, and repeated whitespace as equivalent word
separators. Original headers and values remain unchanged for display and validation.

Each source column is profiled once from at most the first 100 parsed source rows. At
most 25 non-empty, non-formula values per column are checked against the versioned
source-format catalogue, with canonical datatype validation after conversion. See [Field
datatypes](field-datatypes.md).
This sample supports header mapping only; source-format proposals validate the complete column.
The mapping page displays at most the first three of
those values, truncating display text at 80 characters. Formula text and cached formula results are
never profiled or displayed.

Candidate ranking keeps name evidence primary: exact immutable keys rank first, followed by exact
normalized keys, exact normalized display names, and limited shared-name-token matches. Type
compatibility can support or reduce a named candidate but cannot create one. Results are ordered by
rank and then immutable field key, limited to three suggestions, and translated to High, Medium, or
Low confidence without exposing numeric scores.

Only one clear, type-compatible High candidate is selected automatically. Near ties remain
unmapped. When source columns compete for one target, only a strictly stronger match may be
selected; ties require a user choice. Required and identifier status affects only the completeness
summary, never candidate ranking. Explicit manual mappings and `Ignored` choices remain authoritative
after server-side identity, cardinality, required-field, and Dataset-scope validation. Mapping and
submitted source-format completeness checks live in `DatasetRecordImportService`, which returns
structured issue codes and the field metadata needed by the wizard. The web layer translates those
codes into presentation text; it does not repeat validation or open the service unit of work.

The assistant uses no external AI, machine learning, telemetry, network service, saved aliases, or
durable mapping templates. Profiling is bounded independently of the 10,000-row import limit and
does not add database queries per source column or candidate. Existing deterministic Dataset field
order is consumed as supplied; REF-121 can later replace that order centrally without stored order
state in the import feature.

## Guided wizard (REF-134)

The four-step browser wizard is rendered by `pages/datasets/import/wizard.html` and its step
includes. `DatasetRecordImportService.navigate()` checks reachability against the secured
transient state. URL parameters and hidden/form controls never establish completion. CSV and
single-visible-worksheet XLSX imports automatically complete the worksheet step; multi-worksheet
files require an explicit selection. Unknown source columns default to **Do not import**, while
ambiguous recognized matches still require a choice. Required and identifier fields remain
mandatory and target mappings remain one-to-one.

Successful submissions redirect to a GET step URL, so refresh does not repeat the upload or
confirmation. Mapping navigation submits current choices, including Back. Merely viewing an earlier
step does not invalidate later results. Changing worksheets clears mapping and validation;
submitting mappings validates the whole source. Returning through the dataset import entry point
resumes at preview when a submitted mapping exists; otherwise it resumes the reachable active
step. This distinction prevents incomplete uploads from bypassing worksheet or mapping decisions.

A replacement is parsed before the old token is invalidated. Rejected uploads leave the old state
available. Accepted replacements use a new opaque token and TTL, preserve only exact worksheet-name
and source-header matches, and always require mapping review and fresh validation. A preserved
manual target takes precedence over a new automatic suggestion. Removed/renamed headers and removed
target fields cannot retain stale mappings. Store capacity and optimistic state comparisons also
apply to replacements, including concurrent requests. Cancel and logout revoke the relevant state;
expiry is checked on store access and updates. Activity refreshes the 15-minute idle TTL;
an expired import reports the timeout and links to that Dataset's upload entry point.

Preview revalidates without writes, displays up to 50 compact record rows per page, and opens
read-only record details on demand. Invalid previews display only a summary;
a large named native dialog contains the existing capped report (first 100 failing **source rows**,
all messages for each reported row). Submitted values and readable identifiers are escaped;
formula expressions and cached formula values are never exposed. Correct the source externally.

The browser confirmation endpoint requires reachable Preview & import (step 4) as well as valid
validation state. The service's direct `confirm()` call remains the explicit confirmation entry
point for non-browser callers; the browser additionally passes `require_confirmation=True`.
Confirmation atomically claims the transient state before entering the existing serialized dataset
transaction. Other requests cannot update, cancel, replace or confirm an in-flight claim. A domain
conflict rolls back and clears validation; a technical failure releases the claim for an explicit
retry within the refreshed idle TTL. Logout/expiry during processing cannot resurrect state.
After success,
the store retains only the bound completion receipt (count and display metadata) until expiry,
removes source bytes/mapping/schema data, and rejects any further mutation using that token.
This receipt supports refresh of the success page; it is not an executable import token or a durable
draft. Persistence, identifier uniqueness and revision 1 are unchanged. REF-149 confirmation
emits exactly
one transactional `dataset.records_imported` summary on the Dataset, with format and committed
Record/rejected-row counts; it emits no per-Record events. Failures/cancellation/replay emit no
successful summary. See [audit policy](audit-policy.md).

`tests/test_import_wizard.py` covers navigation, manipulated requests, exact replacement compatibility,
resume/cancel/sliding expiry/logout, paged previews, report boundaries, explicit confirmation, replay,
conflict recovery and safe retry. The existing REF-81 tests retain parser/security, atomic rollback,
audit and PostgreSQL concurrency coverage. `tests/test_import_wizard_javascript.py` exercises mapping
completion, duplicate submission and the report dialog's keyboard/focus behavior using Node.

## Dataset deletion (REF-141)

Deletion and confirmation serialize on the Dataset lock. A confirmation offered after deletion
fails closed. After claiming a token, confirmation rechecks the original Dataset UUID under
that lock; reusing a deleted Dataset key cannot transfer the token to a replacement Dataset.
An import that commits first may subsequently be deleted in its entirety; a deletion that
commits first permits no imported rows or import-success event. Tokens retain their existing
actor/session/UUID/TTL binding and replay protections. Inaccessible staged memory is removed
by the existing lazy TTL cleanup or session revocation; no staged upload file exists.
This is logical invalidation on use, not an immediate memory-erasure guarantee. No new cleanup
service is introduced, and no cleanup is represented as part of the database commit.

## REF-83 interaction

Imports continue writing only the concept tables. Preview fingerprints include the
Dataset revision as well as Field/identifier configuration. Confirmation rejects a
revision changed by metadata, Field/Record operations, another import or version lifecycle
changes, and requires review again. Authorization and the original Dataset UUID are still
checked under the same lock. A committed import increments the Dataset revision once;
rollback leaves both unchanged. Existing Scheduled/Current/Expired snapshots are never
updated by import. Administrative access changes remain subject to current authorization.

## Source formats within Map columns (REF-154 / US-108)

The flow remains Upload → optional worksheet selection → Map columns → Preview
→ Confirm. **Advanced options** opens/closes all applicable format disclosures;
**Change format** opens one Field below its mapping. Collapsed rows identify the
datatype, proposed format and one compact source-to-canonical conversion.
String has no source-format disclosure. Boolean and Float expose their fixed profile
and examples without a meaningless selector.

Expanded native `details` controls contain a labelled source-format group above
up to three source/stored pairs. REF-158 replaces the temporal Cartesian dropdown
with **Source type**, **Date format** and **Time format** native controls. Both sides come from
the actual
staged upload, never fixtures or persisted examples. Each compact value is
limited to 80 characters. Longer values have an escaped native disclosure,
bounded by the 4096-character parser limit. On narrow screens each pair stacks
source first, then stored value; pairs never become two independently reordered
lists. All controls retain native keyboard behavior and visible focus. Escape
closes a format disclosure and returns focus to its summary. An unresolved
format opens its affected disclosure and focuses the first affected selector.
The aggregate button exposes expanded/controlled state and is enabled by JS;
individual native disclosures and submission work without JS. When no mapped Field
has a choice, Advanced options is hidden; the shared button component respects
the native `hidden` attribute, including after mapping changes.

The browser POSTs mapping and source choices to the token-bound `/formats`
review route to recompute examples using server parsing. This route reauthorizes,
requires CSRF, validates form shape and changes no state or durable data.
REF-158 adds datatype validation through the existing Field-list service; the
audit inventory records that ordinary read, without a new audit event. Prior
requests are aborted/ignored after newer input, including timezone typing before
change/blur, so a delayed response cannot replace current input or steal focus; source content
never becomes
HTML through string interpolation. Jinja renders the same shared format partial
for initial and updated rows. Failure leaves choices available for authoritative
submission. Mapping changes clear that column's transient format controls.

**Continue to preview** submits the selected components. The HTTP adapter composes
them into the same canonical profiles used by Preview/Confirm and remembered
proposals. Existing direct canonical submissions remain supported. Every column
requires exactly one `mapping_N`, `source_N`, `timezone_N` and `fold_N`. Temporal
`source_N=text` additionally requires exactly the datatype's `date_N` and/or
`time_N`; `native` and Timestamp `offset` reject all date/time components. A
canonical identifier rejects accompanying components. Unknown control names,
duplicate values, missing parts and datatype-incompatible identifiers fail closed.
Hidden/disabled text selections are not submitted for native/offset sources.
Live review sends URL-encoded text controls, matching normal Preview submission;
no uploaded file is sent again during format review.
These two POST routes have a bounded 250 KiB body and at most 1,502 multipart
parts (six controls × the existing 250-column limit, plus CSRF/navigation).
The prior generic 16 KiB envelope could reject valid wide component forms.
The upload's 10 MiB limit and other routes' 16 KiB limit remain unchanged.
Nonempty timezone/fold values are rejected unless the profile is a local Timestamp;
they are no longer silently discarded. The service independently rejects irrelevant
zone/fold metadata. Local IANA and DST parsing rules remain unchanged.

Accepted mapping/format submissions invalidate dependent Preview/Confirm state
through the existing staged-state update. The live example review remains read-only;
it does not itself replace the previously submitted mapping. No database migration,
new public API, new step, durable draft or persistent example is introduced.

**Continue to preview** validates the selected profiles. Preview and confirmation
use the same source parser and immutable choices. Complete-file checks remain
authoritative; an invalid value outside the three examples fails the import.
Unknown/ambiguous detection and timezone/fold decisions block progression at
Map columns. An explicitly selected profile with invalid rows retains the
existing capped Preview error report as well as per-field format feedback when
returning to mapping. Formula and null defenses are unchanged. No separate
Simple/Advanced mode or extra wizard step is introduced.

### Remembered proposals and migration

Migration `0017_ref_154`, after `0016_ref_84`, adds only
`dataset_field_source_formats(field_id PRIMARY KEY/FK, choice JSON)`. The choice
contains a versioned profile ID and, for local Timestamp profiles, bounded IANA
timezone/fold selections. It stores no examples, source bytes, filenames, field
configuration, output format or permissions. Invalid/obsolete proposals are
not selected unless the new whole column conforms to the current catalogue.
The editor can always replace a proposal.

The existing DatasetField persistence port reads proposals in a batch and
writes them only inside the successful import's serialized write Unit of Work,
after authoritative complete validation. Records, values, proposals, Concept
revision/content timestamp and the single summary audit event commit together.
Preview, source review, invalid files, cancel, expiry, stale/racing failures and
rollback do not update proposals. Import choices are deeply immutable in staged
state. No preference lives solely in process memory.

Proposals are operational metadata, separate from versioned Field definitions.
Creating a new Concept preserves proposals for the exact Field identities
retained from its snapshot; it neither snapshots old choices nor grants new
rights. Field/Dataset deletion cascades the corresponding proposals. REF-73
still authorizes current owning-team members and active Community admins; legacy
editor/admin Grants remain inert. Import-format selection invokes no Dataset
administration or output-configuration operation.

Stop writers and take a restorable backup, then use the existing configured
Alembic upgrade/startup process. Upgrade adds an empty table without rewriting
Records, Fields, published versions or audit. With writers stopped, downgrade
`0017_ref_154` to `0016_ref_84` drops only remembered proposals; canonical Records
and audit remain intact. Re-upgrade starts with no remembered proposals. Restoring
a matching backup is required if those suggestions must also be retained.
Historical migrations remain unchanged. Startup schema checks and preflight
commands recognize the new revision; historical test fixtures explicitly
remove the new table through its real downgrade before exercising old schemas.

The REF-158 completion review retains distinct source examples (at most three),
with full-column validation independent of this display cap. Individual disclosure
always exposes its examples; Advanced options is only an all-Fields disclosure
action, not a separate import mode. Without JavaScript, detected temporal choices
can reach Preview; an explicit message explains how to enable corrections/live
examples or replace the source file. See the REF-158 report for current evidence.


## Import usability and idle expiry (REF-259)

The browser flow has four steps: Upload file, Select worksheet, Map columns, and Preview &
import. The preview is the confirmation surface; `/step/5` is no longer a supported wizard
endpoint. Mapping guidance, source filename/worksheet, completeness and the live blocker message
are visible beside the mapping action. Dataset fields appear in one flat select, with suggested
fields first, then the remaining Configure-order fields. A field selected by another source
column is hidden until released. The existing source-column **Do not import** choice remains
supported. Optional destination Fields may stay unmapped (REF-260); mapping validation and
completeness share the required and identifier Field rule. Preview lists omitted Fields with:
**Not in file:** followed by their names and “Existing records keep their values; new records
leave these fields empty.” When Changed + Unchanged is zero, the explanation is “These fields
stay empty.” The ready mapping status also counts Fields not in the file, with singular/plural
labels, only when the count is nonzero and the import is a Draft import (not a mapping target
update).

The preview keeps summary counts and compact Identifier/Status rows, with changed field names as
the only third-column content. Opening a row displays escaped read-only values in a keyboard-
accessible side panel. Search applies automatically; a status selector appears only when multiple
change statuses exist. Pagination and filtering revalidate from the staged source and perform no
writes. The Import records action confirms directly from Preview & import. Deletion acknowledgement
remains unchecked by default and both the exact confirmation token and acknowledgement are checked
again by the server before the serialized write transaction.

The staged 15-minute timeout is an idle timeout: successful reads and state updates refresh it.
This includes wizard navigation, source-format review, preview, and its search/status/page requests.
No upload bytes are persisted, logout and Concept/session revocation still invalidate state, and the
existing cleanup model is unchanged. An expired token known to its bound actor/session/Dataset gets
a 410 response with the configured idle duration and a link to upload again for that Dataset; an
unknown or differently bound token remains indistinguishable from an unavailable token.

Record-table Field metadata uses a compact, accessible info-button popover in the sticky header;
the header does not expand when details open, and inline metadata elsewhere is unchanged. No database
migration or public route contract is added. Existing confirmation, authorization, CSRF and atomicity
checks remain authoritative.

### REF-159 presentation and form-envelope preservation

REF-159 replaces the resting proposal/examples line with clean, transformed,
undecided and failing verdicts. Full non-empty counts and failures come from the
existing whole-column validation, excluding formula cells as before; formulas
still fail authoritative import validation. Only bounded examples enter verdicts.
Candidate readings reuse the existing detection scan and canonical parser output,
with a hard presentation boundary of two through four matches. No second scan,
new detection rule, persisted content, parser profile or preference is introduced.

Canonical `source_N` radios replace the source-type selector for limited ambiguity;
they have no accompanying Date/Time components. A checked hidden empty radio
preserves the strict one-value envelope while no interpretation is selected.
Selecting a real reading natively unchecks that placeholder. Timezone/fold and all
other required controls continue to submit. Missing/duplicate/unknown/incompatible
inputs keep the existing fail-closed handling and 250 KiB / 1,502-part limits.

The JavaScript submit enhancement opens/focuses unresolved choices and blocks
Preview locally; the server independently rejects unresolved interpretations.
Live format review remains read-only. Accepted Preview submissions still invalidate
previous Preview/Confirm state through the existing secured staging service.
Show/Hide all formats keeps undecided/failing disclosures open on a global close;
individual Done/Escape may close them. Native summaries and selected radio
submission work without JavaScript. See the archived REF-159 report for evidence.
No migration, schema, audit, authorization, token, TTL or transaction change.

## Concept discard (REF-155)

Staged uploads, wizard state, previews, confirmations and completion receipts now
bind the original Concept UUID. Checks at staged reads and inside Confirm's locked
transaction reject an absent or replacement Concept. Discard HTTP success purges
local Concept-bound state, including busy claims; finishing an old claim cannot
restore it. Other service callers/processes have immediate logical invalidation;
physical memory cleanup follows obsolete-state lookup, resume, session revocation
and the existing lazy 15-minute TTL. There are no staged upload files.

Discard removes source-format proposals with their working Fields explicitly.
Their values are not versioned/restored from historical snapshots. It creates no
import success audit. A winning import commits all Records/proposals and its audit
first and makes the discard confirmation stale; a winning discard prevents import
persistence. See the archived REF-155 implementation notes.

## Complete new-version imports (REF-160)

The Concept has one **Import** action. The file defines the complete desired set: empty tables
receive additions, while populated tables use the same full comparison for additions, changes,
unchanged Records and retained deletions. Both the original `/records/import` address and
`/concept/records/import-new-version` start the `new_version` engine; neither offers append-only
behavior. The existing Python create-only mode remains compatible for programmatic callers.
Staging never creates a Concept or silently converts an active legacy staged import; cancel that
upload first. Required and identifier Fields must be mapped exactly once; identifiers must be
complete and non-null. Optional Fields may be omitted: matching active Records keep their
current Draft values, while Added Records have no value for those Fields. A previously Deleted
identifier is Added without recovering its retained values. Mapped blank optional cells clear a
value; mapped blank required cells still produce row errors. Extra columns require explicit
Ignore. An empty file cannot mark every Record Deleted. Existing source formats, Date/Time
composition, archive limits, TTL and full-file validation apply unchanged.

One server-side eligibility rule guards the action, upload, preview and confirmation. For a
Dataset with at least one non-cancelled version, Record mutations (including edit/revert, Undo
and create-only import) and a completed new-version import block further attempts. Field-only
changes preserve eligibility. A new Concept on a Dataset with no non-cancelled version remains
eligible after Record mutations and completed imports; every preview compares the file with the
current Draft, and confirmation replaces the previous import result. Confirmation rechecks the
same rule under the Dataset lock. Every migrated Concept has unknown mutation history and is
deliberately ineligible, even when its Dataset has no non-cancelled version:

> **Import unavailable**
> This Concept was created before new-version imports were introduced, so REFRNCR cannot
> reliably verify that its Records are unchanged. Publish or discard this Concept, then
> create a new Concept to import a new version.

This is a one-time compatibility boundary, not an error. See
[ADR-016](../architecture/adr-016-complete-record-import.md).

Matching uses the complete configured identifier and existing canonical typed keys.
Changed identifier means Deleted plus Added. Canonically identical values are Unchanged;
those Records receive no writes or revision increments. A complete comparison yields
Added/Changed/Unchanged/Deleted/Error counts. Preview is read-only, with 50 differences
per page, full-identifier search, status filters and old/new changed Field values.
Unchanged is count-only. Display filtering never selects a subset for confirmation.

The existing staged actor/session/Dataset/Concept binding is extended with mode,
Dataset revision, Concept identity/start revision/update timestamp, schema, identifier,
canonical difference digest and a fresh opaque confirmation value. Source bytes,
worksheet, mapping and source formats remain part of the staged state. An explicit
preview submission renews confirmation; re-reading an unchanged preview does not.
Changes invalidate it. Preview uses a coherent read snapshot and rechecks authority and
Concept identity; confirmation repeats authority, eligibility and validation under the
Dataset write lock. Deletions require an initially unchecked checkbox showing the exact
full count. Neither missing nor duplicate/forged confirmation fields can bypass it.

Confirmation applies the entire difference atomically and stores the one-use result on the
Concept. Competing tokens serialize on the Dataset row; at most one can succeed. A process-local
token claim also prevents same-token replay. Rollback includes content, revisions, the result
marker and audit. Changed Records replace only mapped values; omitted values remain untouched in
storage. Comparison uses the resulting values, so omitted Fields never cause false Changed
counts. Unchanged Records receive no write or revision increment. Unique values in mapped Fields
can move between changed/new rows; deleted values remain retained but do not reserve active
unique values. Current and historical snapshots never change. The success redirect opens the
Concept Records table with Added/Changed/Deleted filtering, totals, identifier search and a
clear-filter link.

Exactly one `dataset:dataset.new_version_imported` summary records `mode`, `format`, `added`,
`changed`, `unchanged`, `deleted`, and `total_processed` (source Records plus Deleted Records),
plus `unmapped_fields`: the omitted schema Field keys in Configure order (an empty list when all
Fields are mapped). Each key follows the existing 100-character schema key contract; the list
contains no Field display names or Record values. No per-row import events, values, identifiers,
filenames or source examples are audited. Manual Undo emits `dataset_record:restored`
transactionally.

### Search/filter interaction (REF-136)

Import preview and complete-identifier matching are unchanged. The post-import Records
page retains the original receipt totals and Added/Changed/Deleted selection. Its explicit
`import_results=1` defaults Record status to All. For reference Datasets, Deleted rows and
list counts include only Records from the Draft's pinned baseline, matching Review & publish;
Records added and removed within the same Draft are omitted. Mapping Dataset deletion
display is unchanged. Preview deletion counts, acknowledgement and receipt totals still
reflect every Draft Record removed by the import. General String
search combines with that selection using AND. Clearing
the import-change selection preserves the other criteria; Clear all returns to the normal
Active view. The general Records search now accepts String Fields only, including String
identifiers. Non-text identifiers are excluded from general search. Neither searching nor
clearing criteria changes eligibility, the receipt, revisions, stored data or audit.

### Mapping target updates (REF-239)

`mapping_update` always requires the preview confirmation token, independently of the caller's
`require_confirmation` flag. Revalidation and no-change rejection happen before any persistent
write. Successful confirmation returns the number of changed rows and emits only
`dataset.mapping_targets_updated` with `format`, `changed` and `unchanged` row counts; no target
values or identifiers enter audit context. Rows omitted from the upload are not counted. The
service rolls back records, draft mutation, source formats and audit together on failure.
