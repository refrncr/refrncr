# ADR-015: explicit Dataset concepts and Current-first navigation

Status: accepted by the product owner on 2026-09-11. Related: REF-84, REF-83,
REF-73, REF-149; recovery/discard follow-up REF-155. Extends ADR-014.

## Context

An implicit working copy cannot distinguish a finished publication from an explicitly
started, unchanged editing round. Opening that copy for administrators also makes the
normal Dataset link mean different content for different users. Equality is a change
indicator, not sufficient lifecycle state.

## Decision

Normal Dataset list/detail/Records/export routes select Current for everyone. Explicit
version routes retain immutable snapshot identity; explicit `/concept` routes require
current Dataset-administrator authority and an active concept. There is no session mode.
No Current means no content fallback. Ordinary users cannot see an unpublished Dataset.

Store one active row per Dataset in `dataset_concepts`, with a separate UUID, pinned
nullable `base_version_id`, creator, created/updated timestamps and start revision.
The Dataset primary key guarantees at most one active concept; a composite foreign key
binds a non-null baseline to the same Dataset. No row means no active concept. The
existing working tables hold its content. Unchanged/changed counts reuse REF-83/84.

`DatasetVersionService.create_concept` is the single authorized, serialized create
operation, also called by Dataset creation inside its existing transaction. It chooses
the latest non-cancelled snapshot, restores that snapshot when the inactive working copy
is different, advances Dataset revision, creates the row and commits
`dataset.concept_created` atomically. Duplicate, denied and failed creation emits no
success event. Record revision advances when restoring content, so old forms cannot
silently overwrite the newly initialized copy.

Without any published version, a concept has a null baseline. The UI says **No published
version yet**. Summary and detailed review classify all Fields/Records as Added without
storing a synthetic snapshot. Valid publication creates Version 1; ordinary users see it
only once Current. Creating a concept against Scheduled is allowed; the existing maximum
of one Scheduled publication still blocks another publication until activation.

Every content writer requires the active row inside its existing authorized transaction,
including no-op updates and import confirmation. Publication validates and snapshots
through REF-83, then closes the concept in the same transaction as snapshot and audit.
Rollback retains both concept identity and all content. Governance and export permissions
remain REF-73; Current export uses Current, while administrator concept export uses an
explicit concept URL/selection and retains ADR-012's request audit boundary.

A concept whose Scheduled baseline is cancelled becomes **Stale** by deriving that state
from the pinned baseline's cancellation marker. Cancellation and staleness are therefore
one atomic fact. Its identity, baseline and content remain unchanged; ordinary edits may
continue. Review/publication and replacement are blocked. No automatic rebase, new
concept or snapshot is created. Within this scope that concept cannot be published or
replaced: data is preserved for a later recovery workflow under REF-155 or a separate story.

Migration 0016 follows unchanged 0015. Stopped-writer preflight rejects ambiguous/corrupt
canonical or identifier state before DDL. Equal copies become inactive; differing or
unpublished copies become active without changing any content, audit or snapshot rows.
Migrated creator is null, displayed as System (migration), rather than an invented user.
DDL, conversion and Alembic revision roll back together; retry is safe on SQLite/PostgreSQL.

## Consequences and alternatives

The UI can show a selectable Draft row even before the first edit. The shared
banner identifies all concept content pages. One final system Status column shows
Published, Changed, Added or Deleted using the shared change-status mapping. Its explicit System
sublabel, accessible header and visual boundary distinguish it from Dataset Fields named
Status, Change or Validation; those Fields keep their real values. Deleted rows load only
the matching baseline Records selected by the same diff projection. They have read-only
snapshot details and an explanation; no edit/delete controls or mutation route is added.

The concept count excludes deleted Records; a separate marked-for-deletion count includes
them. Search uses the current identifier of surviving rows and the baseline identifier of
deleted rows, with literal NFC/casefold substring matching. Combined typed identifier order
uses datatype tags for mixed old/new types and stable UUID tie-breaking, then paginates.
Ordinary Record reads retain their existing full-result sorting/memory characteristics.
Queries are batched, with no per-Record database lookups or third comparison algorithm.
Publication still stores surviving content only and closes the concept atomically.

Equality-only state and a hidden session selection were rejected because they cannot
represent an intentional unchanged concept or predictable Current navigation. Duplicating
content into another concept snapshot would add storage without satisfying a requirement.
Persisting a separate stale flag would duplicate the pinned baseline's cancellation state.
The operational stale lockout is deliberate until an authorized recovery workflow exists.
No REST API, scheduler, Cloud feature, new role, dependency or discard operation is added.

## REF-162 metadata decision

[ADR-017](adr-017-current-dataset-metadata.md) supersedes Dataset name/description
version comparison and restoration. Current metadata is maintained independently;
stored historical snapshots remain unchanged.
