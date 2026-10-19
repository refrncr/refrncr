# Dataset versions: domain and controlled upgrade (REF-83)

Foundation: REF-83 is merged in `main` (`a3f8fed`); see its
the archived REF-83 validation report. The local REF-84 extension is documented below
and in the archived REF-84 review implementation report.

An active concept is an explicit editing round backed by the existing Dataset working set.
Its persistent lifecycle is defined in [ADR-015](../architecture/adr-015-explicit-dataset-concepts.md). Each publication stores its complete
name, description, ordered Field definitions (including required/unique flags and canonical
datatypes), identifier membership, stable Record IDs and canonical values. Null is an
absent value row. Snapshots exclude owning teams, memberships, Grants, export access,
creator metadata and working timestamps/revisions. Current permissions are authoritative.
See [ADR-014](../architecture/adr-014-dataset-versions.md).

## Date and lifecycle contract

UTC is the application timezone for effective calendar dates. The existing aware UTC
clock is injectable in the version service. Browser-local timestamp display does not
change validity. Publication accepts today or future, never a past effective date.
An effective date includes that day; an exclusive end boundary is the next non-cancelled
version's effective date. Displayed end date is the preceding calendar day. With multiple
publications effective on the same date, the highest version number wins; earlier ones
have empty intervals and remain Expired history. No calendar date has two Current versions.

For published versions, only cancellation actor/timestamp is persisted as lifecycle state. Scheduled, Current and
Expired are computed on reads, requiring no scheduler. Cancelled snapshots never activate.
One Scheduled version blocks further publication until its date arrives or it is cancelled.
Cancellation rechecks the UTC day under the lock and preserves snapshot and number.
A missing Current snapshot has no historical/future fallback.

Publication/cancellation linearize at their authorized, locked calendar check. Validation,
complete copy and audit are synchronous and commit atomically. A failure rolls everything
back. Version numbers start at one, include cancelled versions and are unique per Dataset.
Dataset revision checks reject stale publication and duplicate submissions. Content equality
uses canonical values and stable Field/Record identities rather than modification timestamps.
A fully reverted change remains an active concept but is not reviewable or publishable. The summary contains only six counts, never a
detailed diff or before/after value storage.

## Operator upgrade

1. Stop every writer and take a restorable backup. Keep writers stopped through upgrade.
2. On schema `0014_ref_145`, run `refrncr dataset-publication-preflight` with the existing
   database configuration (`REFRNCR_DATABASE_URL` or `--database-path`). The read-only JSON
   report lists every invalid Dataset UUID. Correct missing/invalid Fields, required
   identifiers or values through the preceding application version, then retry.
3. Run `refrncr dataset-publication-preflight --upgrade`. The migration repeats the full
   preflight before creating any version tables or rows. An invalid Dataset or failed copy
   rolls back schema, all snapshots and migration revision together on SQLite/PostgreSQL.
4. Every valid existing Dataset receives exactly one complete Version 1, effective on the
   UTC upgrade date, explicitly published by System. Ownership/access and historical audit
   remain untouched. A successful retry/restart does not create another Version 1.

Normal startup runs the same fail-closed migration. Older supported schema paths first run
existing migrations (including REF-73 ownership and REF-145 validation), then the complete
REF-83 preflight in the migration transaction. The standalone preflight requires the preceding
or current schema; it never silently upgrades an older database. Existing operator ownership
mapping conventions remain supported. No historical migration is changed.

Populated downgrade is blocked because running older code would expose conceptdata and
lose published-version semantics. Restore the pre-upgrade backup with its matching application.
Only a version-empty database may downgrade normally.

## Persistence and operational characteristics

Four new tables are `dataset_versions`, `dataset_version_fields`, `dataset_version_records`
and `dataset_version_values`. Composite foreign keys bind values to their version's Record
and Field. Unique constraints cover Dataset/version number, Field key/order, Record identifier
and per-Field unique values. Canonical keys and text retain exact Decimal/integer/time values
on both databases. Immutable result dataclasses and narrow append/cancel ports expose no content
update or individual delete operation; ORM updates/deletes of snapshot rows are refused.
This is an application/persistence contract, not a tamper-proof database for privileged SQL users.

REF-141 deletes versions and all children transactionally with the Dataset, including cancelled
history. Audit has no Dataset foreign key and survives. No orphan snapshot rows remain when FK
enforcement is enabled (the normal SQLite connection factory always enables it).

Publication and comparison use linear reads and memory for the current and preceding snapshots,
plus identifier/unique-value sets. Peak memory is proportional to their complete Field/Record/value
contents, not just Record count. Storage grows with every full publication. SQLite holds its
serialized writer through copying; PostgreSQL holds the Dataset row lock. There is no new hard
Dataset size cap: the import limit is per upload, not a total Dataset or publication limit. Existing
per-datatype limits apply; release notes are capped at 2,000 codepoints. Large synchronous copies
can consume substantial memory and block writes; background publication belongs to REF-86.


## Published reads and browser workflow

`DatasetVersionService` selects content and current REF-73 permissions within one coherent
read Unit of Work. Every active Community user, including Dataset administrators, sees Current snapshots
through normal browser Dataset lists, configuration and Records. Administrators enter
their explicitly active concept through its Versions row. Trusted maintenance helpers
can request the concept explicitly; the web routes never infer that choice from the role. A Dataset without Current content
is absent from their list and its routes return the same non-disclosing 404 as missing data.
Historical viewer/editor/admin Grants do not restore concept access. Current owning-team
and additional export permissions apply equally to all retained versions, never historical
permission copies.

Versions lists show Current/Expired to ordinary users, plus Scheduled/Cancelled to Dataset
administrators. A Scheduled snapshot is read-only for administrators. Cancelled content is
inaccessible even through an administrator's guessed version URL. To avoid disclosing private
Scheduled metadata indirectly, ordinary users do not see a Current version's future end date
until the successor activates. They then see the preceding calendar day as its end date.
Published timestamps use the existing REF-39 local display helper; effective/end dates remain
UTC calendar dates even when the browser timezone differs.

Explicit version URLs combine current Dataset names/descriptions with snapshot ordered Fields, identifier, Records,
values and pagination together. Deleting/reusing a concept Field key or deleting concept
Records cannot redirect a historical read to new objects. Version Record pages contain no content edit or
import controls. Configure can edit current Dataset metadata independently. Their export menu requires current export authority and only permits Current
or Expired versions. CSV, Excel and JSON retain REF-82 serializers and streaming semantics;
filenames include the Dataset key and version number. Existing administrator concept export
remains available from the concept Records page.

A Dataset or Field technical key can be corrected while its first Concept is still unpublished.
Publishing the first version makes those keys permanent. Field-key correction updates the working
identifier definition and retained deleted-Record snapshots in the same transaction; published
version snapshots are never rewritten. A Field key already used by a linked mapping remains
protected because changing it would break that mapping contract.

Publication and cancellation reuse the shared side-panel host, form controls, destructive
button, feedback and focus handling. Both also work as full-page forms without JavaScript.
Publication displays only aggregate counts and requires effective date, reviewed revision and
explicit confirmation; a stale response requests a fresh review. Cancellation rechecks its
calendar boundary in the transaction. Busy status and disabled submit controls prevent repeat
submission visually; revision/locking remain authoritative on the server. CSRF and stable
Dataset identity prevent forged requests and stale forms targeting a reused key.

Lists use one batched Current-window query, active-concept identities and bulk authorization;
query count does not grow with Dataset count. Version history batches publisher names (10
SELECTs including authority/context), and Records use a single joined value read (14 SELECTs
including the three current capabilities). Regression tests compare one/many versions and
Records. Like existing Records pagination, canonical ordering currently materializes the selected
Record set in memory before slicing 50 rows: O(records + values) memory and O(records log records)
ordering work. No claim of database-side record pagination is made. Version history currently
loads all version metadata. Export retains bounded external sorting and spooling instead.

## Detailed concept review (REF-84)

**Review changes** on Versions or the Publish version panel opens a read-only review
for active owning-team members and Community administrators. It compares the active concept
at one revision to its pinned baseline, including Scheduled. Without a published baseline,
Summary and detailed review use all-added counts without a synthetic stored snapshot.
An unchanged concept offers neither review nor publication. Summary, Fields and Records share the same baseline and revision.

The product decision of 2026-09-11 corrects REF-83's shared classification: Field
additions/removals and datatype-only changes count only as Field changes. Records
match stable UUIDs and count as Changed only when canonical content differs in surviving
Fields with the same canonical datatype. Values in retyped Fields are not comparable
and do not enter Record counts or Changed value details. Relative Field moves use deterministic longest-increasing-subsequence matching;
inserting/removing a Field does not mark its neighbours moved. Identifier membership
is versioned; component order remains immutable Field-key order.

Record lists support combined Added/Changed/Deleted filters and a literal, NFC/casefolded
search of old/new identifiers (maximum 200 characters). Values are not searchable.
Sort is status, datatype-aware identifier (new for Changed), then UUID. Field sort is
status, casefolded name, UUID. Lists and value details have 50 entries per page. Changed
details contain only changed surviving values; Added/Deleted show all relevant Fields,
including explicit nulls. Scrollable tables support keyboard navigation, and long values
remain available through native disclosure controls. Shared sidepanels preserve the list
state and opener focus, including when paging values; Escape and Close dismiss them.

Every request reauthorizes and validates revision plus baseline identity/status. Concept
writes, publication/cancellation and Scheduled activation invalidate the review. Stale
responses contain no Before/After content and offer Reload review. Back/Refresh revalidate
through no-store responses and BFCache restoration handling. Publication uses the existing
REF-83 form, CSRF, validation and transaction, with optional review identity checked under
the same write lock. Review is never a mandatory gate or persisted approval status.

Browser review links carry a session/actor-bound HMAC signature, never diff content. The
application factory accepts `review_signing_key` for a shared, securely injected server key
across workers. By default a key is generated per application instance; restart invalidates
old links. Independently generated keys are not interchangeable between workers. Do not
hard-code or expose the key. Responses use `Cache-Control: no-store` and review links use
`Referrer-Policy: no-referrer`.

Review opening is the explicit REF-149 read-audit exception: one atomically committed
`dataset.version_diff_viewed` event containing only actor/Dataset identity and eight bounded
integer context fields. Child navigation produces no events. Audit insert/commit failure
returns no review. See [audit policy](audit-policy.md) and the
archived REF-84 implementation and measured validation report.

Review uses scalar streams and SQL aggregates, bounded Record heaps and paged value reads.
There is no diff storage/cache, full value graph, N+1 query, background job, download, API,
Cloud infrastructure, new role, arbitrary historical comparison or approval workflow.
Opening performs linear canonical integrity scans and holds the Dataset write lock through
audit commit. Child list CPU is linear in candidate identifiers; Field memory is O(schema).
These are explicit limits, not a claim of constant-time or unlimited-size performance.

## Explicit concept lifecycle and migration 0016

Normal links and breadcrumbs select Current. **Versions** is a header action with the
existing history icon; no standalone tab remains. Versions exposes **+ New version**
(the authoritative concept-create operation) only to administrators with no active concept.
Its aligned **Concept** row always appears first, followed by published versions in
descending version-number order, including Scheduled and Cancelled entries. The Concept's
pinned baseline does not affect row placement. All rows use the same columns. Draft/Stale is shown in Status;
unavailable publication metadata is an em dash. The row opens the concept. Review changes
is available on Versions and concept pages; Publish version remains on concept and review
pages. The Publish panel also offers optional review before the first publication.
Versions has no Record-count/date subtitle.

The shared full-width banner appears on concept Records/configuration, Field and Record
forms, import and review/publication pages. **View current version** only changes selection;
it preserves edits. Current and Expired remain read-only, with no Configure/Import/Add
record. Content Fields named Status, Change or Validation are never repurposed. A final
system Status column shows Published, Changed, Added or Deleted. Configure uses the same Field classification for quiet badges.

Publication closes the concept only after successful snapshot/audit commit. A Scheduled
result also closes it; New concept may bind Scheduled but cannot publish a second Scheduled
version. Cancellation of that pinned Scheduled baseline preserves the concept as Stale,
without rebase or base-ID change. Edits remain administrator-only. Publication/replacement
stay blocked until a later recovery workflow; REF-155/discard is not implemented here.

For an existing `0015_ref_83` database, stop writers and back up, then run
`refrncr dataset-concept-preflight` with the existing database configuration. The JSON
report lists invalid Dataset IDs without content. Correct reported data before retrying.
`refrncr dataset-concept-preflight --upgrade` repeats preflight and atomically installs
`0016_ref_84`. Equal working copies are inactive; divergent/unpublished copies become
active with original content preserved. No migration audit event or synthetic snapshot is
created. Null creator displays System (migration). Startup uses the same migration chain;
older supported schemas still run unchanged 0015 first. Active concepts block downgrade;
restore the matching pre-upgrade backup if rollback is required.

## Record system Status and Deleted rows

Status uses the shared change-status mapping: Changed remains Changed, the persisted Removed
value displays as Deleted, Added remains Added, and a surviving unchanged Record is Published. Published snapshot
pages show Published. There is no Complete/Incomplete system badge; existing publication
validation still applies. Configure Field badges retain their existing classifier.

The concept table includes read-only Deleted rows from its pinned baseline. Deleted rows
are muted with a text badge; opening one shows all original baseline Fields/values and
explains that publication removes it. They cannot be edited, and ordinary users cannot
access this concept representation. Deleted/recreated identifiers remain distinct UUID rows.
Deleted data never re-enters working content or the next published snapshot.

The header counts surviving concept Records separately, for example `24 records · 1 marked
for deletion`. REF-136 defaults the Records page to Active; Deleted and All are explicit
Concept selections. Pagination counts every match within the selected set. General search
is a literal, NFC/casefold String substring (maximum 200 characters), including String
identifiers. Non-String identifiers are not included in general search. Only the displayed
version's Field identities/types participate; retained values from an incompatible or
recreated Field do not acquire the replacement Field's meaning. Deleted details still
show the original retained values.
Typed sort continues using each row's source identifier fields, datatype tags and stable
UUID ties. Page links preserve criteria. Metadata counts stay version-wide even when
searching. Current reads remain separate, and no navigation/search/detail audit event
is added; only explicitly opening a new valid detailed review retains the REF-149 exception.

## Discard concept (REF-155)

An owning-team member or Community admin can discard an active Concept only when
a Current version exists, including unchanged Concepts and Concepts whose Scheduled
baseline was cancelled. Without Current, the action stays visibly disabled with
the approved explanation; name and description are never replaced with a fallback.
This supersedes the earlier statement that stale Concepts have no discard workflow.

The signed confirmation binds the Concept, revisions, actor/session, owning team
and Current. Discard atomically deletes working children and the active Concept,
preserves current Dataset metadata and writes one bounded audit; published snapshots and
governance remain intact. It creates no new Concept. The existing explicit creation
policy remains unchanged: Scheduled when applicable, otherwise Current. This also
applies to Create concept after discard, as explicitly confirmed for REF-155. See
the archived REF-155 architecture and validation report.

## REF-160 retained deletions and import eligibility

Deleted Concept Records now retain their own last canonical values and Field definitions
on the existing Record row. Their detail panel provides **Undo delete**, for manual and
imported deletions alike, including Records added and then deleted within this Concept.
Undo uses the active Concept identity and Record revision and repeats maintenance
authorization. Stale, reused-identifier, unique-value and incompatible-schema restoration
fails without changing content. Current/history remain immutable. Publication excludes
Deleted Records; discard and later Concept initialization retain their existing semantics.

Migration 0018 establishes an explicitly approved one-time compatibility boundary: every
existing Concept is excluded from complete new-version import; publish/discard and create
a new Concept to use it. No historical audit attribution is guessed. Physically missing
baseline Records are restored as Deleted using their published baseline values; their
old Concept edits were already lost. New deletions preserve actual last Concept values.
Field-only changes leave a newly created Concept importable. See
[ADR-016](../architecture/adr-016-complete-record-import.md) and
[import security](record-import-security.md#complete-new-version-imports-ref-160).

## Presentation metadata (REF-162)

Migration `0019_ref_162`, following `0018_ref_160`, adds nullable Dataset `icon`
(String 32), and nullable `definition` (String 4000) / `unit` (String 64) to both
working and snapshot Field tables. Field length constraints apply in both engines.
No historical values are inferred or backfilled. Older retained-deletion JSON
payloads deserialize with null metadata defaults. Historical upgrade helpers
explicitly read the old schema before the new columns exist.

Definition and Unit follow Field copy, Concept initialization, publication,
Scheduled/Current/history and retained deletion. Review identifies these as Field
metadata changes without Record changes. Published metadata stays immutable.
The Dataset icon is current presentation metadata: it is shared by Dataset contexts
and does not advance content revision or alter published Record snapshots.

Upgrade through the existing Alembic process. Downgrade to 0018 removes the new
metadata columns and therefore loses their values; back up before rolling back.
The existing populated/lifecycle downgrade safeguards remain in force.


Migration `0020_ref_162` widens Dataset `icon` to String 64 for manual names from the
full pinned Lucide sprite; two supported identifiers exceed the former 32-character
limit. It follows the existing 0019 migration without modifying it. Upgrade preserves
values on SQLite and PostgreSQL. Downgrade refuses names longer than 32 characters
instead of truncating them. This is presentation metadata and does not alter snapshots.

REF-162: Dataset name and description now follow the current icon semantics. They
are editable without a Concept, do not advance content revisions, and cannot by
themselves make a version publishable. Concept creation/discard preserves them;
stored snapshot metadata stays immutable. See [ADR-017](../architecture/adr-017-current-dataset-metadata.md).

## Record criteria (REF-136)

General String search runs within the existing coherent
authorized snapshot, before the existing 50-row pagination. Current and explicit immutable
version routes never load Concept content; Scheduled access keeps the existing maintenance
restriction. Active/Deleted/All operates independently of the retained REF-160 import-change
selection. Its redirect defaults to All so Deleted import results remain included.

The internal `DatasetVersionService.read` keeps its pre-existing All default for callers
that consume the combined Record projection; the Records HTTP adapter explicitly supplies
Active by default. No public Record mutation, import, export or audit contract changes.
No schema/index migration is needed. Full-set canonical sorting remains in Python, with
the existing joined/batched loading. See the archived REF-136 report for URL grammar,
null/schema semantics, performance and validation evidence.
