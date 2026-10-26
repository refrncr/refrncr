# ADR-016: complete Record imports and retained deletions

Status: accepted. Migration compatibility decisions approved by the product owner on
13 September 2026. Never-published Dataset exception approved in REF-276. Extends
ADR-014/015 and REF-155/159.

## Context

REF-160 imports a complete proposed Record version into an explicitly created Concept.
Equality with the published baseline cannot prove that Records have never been mutated:
add/delete and edit/revert erase that evidence. The existing delete physically removed
working rows and displayed missing baseline Records through the version diff. This loses
last Concept values and cannot support Undo delete for an unpublished Added Record.

## Decision

The product owner approved a single **Import** flow on 13 September 2026. Every uploaded
file describes the complete desired Record set. Empty tables naturally produce only
Added differences; populated tables use the same comparison for Added, Changed,
Unchanged and Deleted. There is no append/replace choice in the product. The original
`/records/import` address and the REF-160 address both start this complete-file flow.
The existing Python service's create-only mode remains for compatibility with existing
programmatic callers; no product entry point selects it. Stored mode/audit identifiers
remain stable. This UX consolidation adds no schema migration or dependency.

REF-260 permits omitted optional Fields. Required and identifier Fields still need
mapping. For matching active Records, omitted Fields retain their current Draft values
without a write; mapped blank optional cells clear their values. Added Records have no
value in omitted Fields, including when their identifier matches a retained Deleted
Record. The complete Record set stays: active identifiers absent from the file become
Deleted. Confirmation remains bound to the full difference, including retained values
and revisions. This amends the original all-Fields rule; it does not change the
eligibility decisions below.

For a Concept with at least one non-cancelled version,
one completed import or any Record mutation blocks another import in the same Concept,
including a first import into an empty table. For a new Concept on a Dataset with no
non-cancelled version, Record mutations and completed imports do not block later
imports: each preview compares against the current Draft, and confirmation replaces
the prior import result. A legacy Concept whose `records_mutated` marker is null
remains ineligible because its history cannot be verified. Eligibility is checked
again under the Dataset lock at confirmation. Published versions remain immutable.

Keep the existing Record identity, ports, Unit of Work, canonical values, snapshots and
system-status presentation. Add three nullable storage columns in Alembic 0018:

- `dataset_concepts.records_mutated`: false for a newly created Concept; true after any
  successful Record mutation; null for every Concept that predates migration.
- `dataset_concepts.import_result`: the successful new-version classification totals and
  changed Record UUID/status associations, set under the Dataset lock in the same
  transaction as content and the single import-summary event.
- `dataset_records.deleted_content`: the retained canonical values and Field definitions
  at deletion, on the same Record row. Null means active. It is not a second Record model.

The deleted payload stores canonical-key bytes as hex and existing snapshot Field
metadata. Active normalized value rows are removed after capture, freeing active-only
uniqueness and permitting later Field removal/retyping without losing deleted values.
The Record identifier unique constraint becomes an equivalent partial unique index for
active rows. Reads, validation and publication exclude retained deletions from active
content. The Concept table reads their retained values and exposes Undo delete.

Restore requires the same active Concept UUID, current maintenance authority, matching
Record revision, compatible current Fields, required values and unoccupied unique keys.
It restores the same UUID and canonical values, increments Record/Dataset revisions and
writes one `dataset_record:restored` event. Schema or uniqueness conflicts fail atomically;
no values are coerced or discarded to force restoration. Publication snapshots active
content only. Creating the next Concept initializes from its selected snapshot whenever
retained deletions remain. Discard uses the existing REF-155 transaction.

## One-time compatibility boundary

All pre-migration Concepts are ineligible for new-version import, including apparently
unchanged ones. Historical Record audit lacks Dataset IDs; do not reconstruct attribution.
This is a one-time compatibility boundary, not corruption. The UI explains that users
must publish or discard, then create a new Concept. Field-only changes do not block a
new Concept. Mutation of a legacy Concept preserves its null marker and explanation.

Missing baseline Records are reinserted as Deleted with published baseline values.
Their last Concept values were physically lost by the old implementation and cannot be
recovered. The product owner explicitly approved this fallback. Existing snapshots,
audits, active content and old identifiers are preserved. No synthetic success audit is
written. Test the real migration and new-Concept path on SQLite and PostgreSQL.

Stop writers and back up before upgrading. SQLite DDL/data and PostgreSQL DDL/data run
inside their existing transactional Alembic boundary. Historical publication/Concept
preflights explicitly use their old schema projections; they must not query later columns.
Downgrade refuses retained deletions or active completed imports that an older version
cannot represent. Restore a matching backup if such state must survive a rollback.

## Alternatives and consequences

Inferring eligibility from diff counts or reconstructing historical audits was rejected:
neither proves absence of earlier mutations. Physical deletion cannot preserve Undo.
Keeping deleted normalized values would block existing Field deletion/retyping and
active uniqueness. A separate tombstone/Record table duplicates identity and violates the
single working Record model. Storing an entire replacement Concept snapshot adds another
working-content architecture without a requirement.

The retained payload increases storage proportional to deleted content, including Field
metadata. Existing full-result Concept sorting/search memory characteristics remain.
Import input keeps existing byte/row/column bounds; comparison streams stored Records and
retains identifier sets plus at most 50 display differences. Confirmation is serialized
per Dataset and can take longer for large deletions. No public REST API, new dependency,
role, tenant infrastructure or Cloud-only feature is introduced.
