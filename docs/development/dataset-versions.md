# Dataset versions: domain and controlled upgrade (REF-83)

Implementation: REF-83 on `feat/ref-83-dataset-versioning`; see the
[validation report](ref-83-report.md) for local checks and CI status.

The concept is the existing Dataset working set. Each publication stores its complete
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

Only cancellation actor/timestamp is persisted as lifecycle state. Scheduled, Current and
Expired are computed on reads, requiring no scheduler. Cancelled snapshots never activate.
One Scheduled version blocks further publication until its date arrives or it is cancelled.
Cancellation rechecks the UTC day under the lock and preserves snapshot and number.
A missing Current snapshot has no historical/future fallback.

Publication/cancellation linearize at their authorized, locked calendar check. Validation,
complete copy and audit are synchronous and commit atomically. A failure rolls everything
back. Version numbers start at one, include cancelled versions and are unique per Dataset.
Dataset revision checks reject stale publication and duplicate submissions. Content equality
uses canonical values and stable Field/Record identities rather than modification timestamps.
A fully reverted change is not publishable. The summary contains only six counts, never a
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
read Unit of Work. Active owning-team members and Community administrators retain the
existing concept management context. Every other active Community user sees only Current
snapshots in Dataset lists, configuration and Records. A Dataset without Current content
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

Explicit version URLs keep snapshot names, descriptions, ordered Fields, identifier, Records,
values and pagination together. Deleting/reusing a concept Field key or deleting concept
Records cannot redirect a historical read to new objects. Version pages contain no edit or
import controls. Their export menu requires current export authority and only permits Current
or Expired versions. CSV, Excel and JSON retain REF-82 serializers and streaming semantics;
filenames include the Dataset key and version number. Existing administrator concept export
remains available from the concept Records page.

Publication and cancellation reuse the shared side-panel host, form controls, destructive
button, feedback and focus handling. Both also work as full-page forms without JavaScript.
Publication displays only aggregate counts and requires effective date, reviewed revision and
explicit confirmation; a stale response requests a fresh review. Cancellation rechecks its
calendar boundary in the transaction. Busy status and disabled submit controls prevent repeat
submission visually; revision/locking remain authoritative on the server. CSRF and stable
Dataset identity prevent forged requests and stale forms targeting a reused key.

Lists use one batched Current-window query, grouped concept counts and bulk authorization;
query count does not grow with Dataset count. Version history batches publisher names (10
SELECTs including authority/context), and Records use a single joined value read (14 SELECTs
including the three current capabilities). Regression tests compare one/many versions and
Records. Like existing Records pagination, canonical ordering currently materializes the selected
Record set in memory before slicing 50 rows: O(records + values) memory and O(records log records)
ordering work. No claim of database-side record pagination is made. Version history currently
loads all version metadata. Export retains bounded external sorting and spooling instead.
