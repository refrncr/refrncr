# Record import security

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
source-format catalogue, with canonical datatype validation after conversion. See [Field datatypes](field-datatypes.md).
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
after server-side identity, cardinality, and Dataset-scope validation.

The assistant uses no external AI, machine learning, telemetry, network service, saved aliases, or
durable mapping templates. Profiling is bounded independently of the 10,000-row import limit and
does not add database queries per source column or candidate. Existing deterministic Dataset field
order is consumed as supplied; REF-121 can later replace that order centrally without stored order
state in the import feature.

## Guided wizard (REF-134)

The five-step browser wizard is rendered by `pages/datasets/import/wizard.html` and its step
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
expiry is checked on store access and updates. Navigating does not extend the original TTL.

Preview revalidates without writes, displays ten canonical records with mapped fields in dataset
order, and uses the normal em-dash marker for empty cells. Invalid previews display only a summary;
a large named native dialog contains the existing capped report (first 100 failing **source rows**,
all messages for each reported row). Submitted values and readable identifiers are escaped;
formula expressions and cached formula values are never exposed. Correct the source externally.

The browser confirmation endpoint requires the reachable final confirmation step as well as valid
validation state. The service's direct `confirm()` call remains the explicit confirmation entry
point for non-browser callers; the browser additionally passes `require_confirmation=True`.
Confirmation atomically claims the transient state before entering the existing serialized dataset
transaction. Other requests cannot update, cancel, replace or confirm an in-flight claim. A domain
conflict rolls back and clears validation; a technical failure releases the claim for an explicit
retry within the original TTL. Logout/expiry during processing cannot resurrect state. After success,
the store retains only the bound completion receipt (count and display metadata) until expiry,
removes source bytes/mapping/schema data, and rejects any further mutation using that token.
This receipt supports refresh of the success page; it is not an executable import token or a durable
draft. Persistence, identifier uniqueness and revision 1 are unchanged. REF-149 confirmation emits exactly
one transactional `dataset.records_imported` summary on the Dataset, with format and committed
Record/rejected-row counts; it emits no per-Record events. Failures/cancellation/replay emit no
successful summary. See [audit policy](audit-policy.md).

`tests/test_import_wizard.py` covers navigation, manipulated requests, exact replacement compatibility,
resume/cancel/expiry/logout, ten-row previews, report boundaries, explicit confirmation, replay,
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
String, Boolean and Float have no meaningless multi-format selector.

Expanded native `details` controls contain a labelled **Choose source format**
select above up to three source/stored pairs. Both sides come from the actual
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
requires CSRF, validates form shape and changes no state or durable data. Prior
requests are aborted/ignored after newer input, including timezone typing before
change/blur, so a delayed response cannot replace current input or steal focus; source content never becomes
HTML through string interpolation. Jinja renders the same shared format partial
for initial and updated rows. Failure leaves choices available for authoritative
submission. Mapping changes clear that column's transient format controls.

**Continue to preview** submits the selected profiles. Preview and confirmation
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
