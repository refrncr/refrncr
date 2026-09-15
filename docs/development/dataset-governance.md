# Dataset governance (REF-73)

Teams are data domains in the single Community installation. Every Dataset has one owning team.
All active, non-deleted users can discover/read metadata, schema, identifiers and records.
Creating a Dataset requires a current membership in its chosen team; administrators may choose
any existing team. One eligible team is selected automatically, multiple teams require a choice,
and zero teams blocks creation with a link to Teams. Teams and memberships are never auto-created.

Current owning-team members maintain metadata, fields, identifiers and records, including imports,
and may export. Community administrators maintain/export all Datasets and alone reassign ownership
or manage extra export access. A direct user or team export entry permits CSV, Excel and JSON
downloads but no maintenance. Removing one entry leaves other applicable access intact. Inactive
and deleted users have no capabilities, even if memberships/export entries remain stored.

`DatasetAuthorizationService` is the authoritative framework-independent decision layer. Mutation
services pass their locked Unit of Work into it; route/template flags only present these decisions.
An in-progress export uses its consistent database snapshot. New operations see current membership.
The generic authorization adapter delegates Dataset checks to this layer. Historical Dataset
`viewer`, `editor`, `admin` Grants are retained inert: they affect no visibility, maintenance or
export decisions and are not migrated into export entries. Generic non-Dataset Grants retain
their behavior. `owner_user_id` means creator only and never authorizes a request.

## Ownership concurrency and audit

The Edit dataset form saves name, description and (for administrators) owning team together.
It captures `expected_owner_team_id` when opened. The server requires exactly
one expected and target ID, checks authorization and current persisted ownership under the Dataset
lock, validates the target in the service, and executes a compare-and-set. Stale requests fail
entirely, including when their proposed target equals the current owner. A valid same-team no-op
changes neither `updated_at` nor audit. Successful reassignment changes metadata `updated_at` but
not content freshness, and records old/new team IDs. Ownership and audit commit or roll back together.
SQLite uses the existing serialized write adapter; PostgreSQL uses row locks. No Dataset revision
is introduced. Existing record revisions and schema/import concurrency checks retain their contracts.

Creation audits include creator and team IDs. Explicit export grant/revoke audits include Dataset,
subject type and subject ID. Team deletion cascades its export entries as part of the audited team
lifecycle; deleting a team that owns Datasets is blocked until explicit reassignment.

## Operator upgrade

Migration `0012_ref_73` follows `0011_ref_130`. It adds required team ownership, constrained export
access and nullable structured audit details. Historical creator references, records, schema,
timestamps, audit events and non-Dataset Grants are preserved.

1. Stop every application/CLI writer and take a restorable database backup. Use the same database
   configuration for preflight and upgrade. Keep writers stopped until the upgrade succeeds.
2. Run the read-only report (exit 1 means unresolved ownership):

   ```shell
   refrncr dataset-ownership-preflight --database-path /path/to/refrncr.db
   ```

   For PostgreSQL, supply `REFRNCR_DATABASE_URL` through existing environment/secret configuration
   and omit `--database-path`. The same command supports `--database-url` where appropriate.
3. Inspect every `unresolved` Dataset key and the available `teams` IDs/names. A creator with exactly
   one current membership resolves automatically; zero or multiple teams require explicit choice.
   Create necessary teams through the existing supported team workflow before the maintenance
   window if needed; the migration does not create teams.
4. Save a JSON object mapping stable Dataset keys to **existing team IDs**, for example:

   ```json
   {"countries": "dd084b3a-8589-4fd4-9850-63924139be70"}
   ```

   Replace the example UUID. Unknown Dataset keys, unavailable teams, duplicate JSON keys and
   invalid mapping structures are rejected. Explicit mappings can also override an automatic
   candidate after an operator decision.
5. Validate the mapping, then perform the upgrade:

   ```shell
   refrncr dataset-ownership-preflight --database-path /path/to/refrncr.db --mapping owners.json
   refrncr dataset-ownership-preflight --database-path /path/to/refrncr.db --mapping owners.json --upgrade
   ```

   The migration recomputes/validates ownership before DDL. Any unresolved Dataset or later failure
   rolls back the schema and backfill together on SQLite and PostgreSQL. Fix the cause and rerun;
   no fallback team or partial ownership is persisted. Programmatic Alembic callers may provide
   `config.attributes["dataset_owner_mapping"]` with the same mapping.
6. Restart the upgraded application and verify owning teams and access. Additional export access
   starts empty; historical viewers do not silently gain export access. Normal `refrncr start`
   upgrades without a mapping only when all existing ownership can be resolved automatically.

Downgrade with existing Datasets or legacy Dataset Grants is intentionally blocked: running old
code could reactivate those roles and would lose team/export semantics. To roll back a deployment,
stop writers, restore the pre-upgrade backup and run its matching application version. Destructive
cleanup followed by an empty downgrade is only appropriate for disposable databases.

## UI and validation

Owning-team display names appear in overview, breadcrumbs and configuration. Creation reuses the
existing sidepanel, form and feedback components with a full-page fallback. Administrators change the
owning team in the Dataset details edit form and manage export access on the Access management tab.
Read-only users can open configuration; maintenance and export actions follow separate capabilities.
No design tokens or separate JavaScript controller are added. The shared controller has opt-in selection behavior. The shared `page-header-actions-end` variant
keeps the configuration header actions right-aligned, including after wrapping. The team-choice macro
shares creation/reassignment markup.

Tests in `test_dataset_governance.py` and `test_dataset_governance_migration.py` exercise both SQLite
and PostgreSQL using `REFRNCR_TEST_POSTGRES_URL`. Existing Dataset and lifecycle regressions supply
explicit owning-team fixtures; legacy migration tests construct historical fixtures separately from
the production downgrade guard. Browser tests cover the Access management tab at desktop and
320px widths: grant, duplicate error focus, revoke and the no-JavaScript fallback. Local checks do not replace GitHub Actions. The part-2 review adds concrete service-boundary, after-flush rollback, concurrent revocation and bounded-query tests in `test_dataset_governance_review.py`.

See [ADR-011](../architecture/adr-011-dataset-team-governance.md) for architectural tradeoffs.

The configuration header places the grey `Access management` action at the upper right.
The standalone owning-team action and explanatory access paragraph are removed from that page.
Ordinary owning-team members see the team read-only in Edit dataset. Combined admin saves reject
stale ownership before any metadata changes and commit both changes and audit records atomically.

## Review test execution

Run the complete pytest suite with `REFRNCR_TEST_POSTGRES_URL` set to the isolated PostgreSQL
16 test database; the dual-engine fixtures exercise SQLite as well. The connection smoke test
also honors this URL. With the variable unset, the existing optional PostgreSQL fixtures skip;
this is not sufficient PostgreSQL validation.

The optional real-browser scripts use `tests/browser_table_rows_app.py` on loopback with disposable
in-memory data. `dataset_creation_browser.cjs` covers zero/one/multiple-team users and maintenance
controls; `access_management_browser.cjs` covers the Access management tab (grant, duplicate,
revoke, keyboard and no-JavaScript fallback). Use a fresh fixture for each
creation run. They follow the external Playwright setup in the UI foundation guide and add no
runtime dependency. Screenshots belong outside the repository.

## Permanent Dataset deletion (REF-141)

Current owning-team members and active Community administrators may delete a Dataset from
the Edit dataset danger zone. Ordinary viewing, creator metadata, legacy Grants and export
access do not authorize deletion. The separate confirmation shows current Field/Record counts
and requires the exact current Dataset name plus unchanged expected metadata timestamp.
Deletion is permanent and atomic with its one audit event. All operational dependencies
are removed; users, teams, memberships and append-only history remain. See the
[dependency inventory and confirmation contract](ref-141-report.md). No soft delete, undo,
recovery, retention policy, background job or migration is introduced.

## REF-83 publication authority (product-owner clarification, 2026-09-10)

The same active owning-team members and Community administrators who maintain concepts
may publish and cancel Scheduled versions. REF-83's earlier separate editor/publisher
wording is withdrawn. No editor/publisher role or per-user editing exception is added;
legacy Dataset Grants remain inert. `DatasetAuthorizationService.can_maintain_dataset`
is the authoritative decision for all three actions, rechecked inside the locked UoW.
Current ownership/membership/export access is administrative data, never snapshot content.

REF-83 adds a Dataset revision for content and version lifecycle operations. Metadata,
Field definitions/order/identifier membership and Record/import changes increment it;
normalized no-ops do not. Publication compares it under the existing Dataset lock.
Ownership/access changes are excluded from content comparison and continue using their
existing authorization and concurrency contracts.


## REF-84 concept authority

Current is the normal browser selection for every role. Active owning-team members and
Community administrators alone can create/open/edit an explicit concept; legacy viewer,
editor and export grants do not grant this capability. The create operation serializes
on Dataset and commits its row with one `dataset.concept_created` event. Every content
writer also checks active concept state inside its existing authorized write transaction.
Old mutation URLs without a concept return a safe conflict and New concept/Current links.
Denied concept requests are non-disclosing. Governance operations remain independent of
content lifecycle. Concept export remains administrator-only through explicit selection;
ordinary export authority continues to apply to Current/Expired snapshots only.

Cancellation of a concept's Scheduled baseline atomically makes it Stale through the
existing cancellation marker. Administrators retain access to every edit, while ordinary
users see only eligible snapshots. No rebase/discard/recovery permission is invented.
The stale concept cannot be published or replaced in this scope; data remains preserved
for REF-155 or a separate recovery story.

## REF-155 discard authority

Discard uses the same owning-team/Community-admin maintenance capability, rechecked
under the Dataset lock. Legacy Viewer/Editor grants and export access are insufficient.
It additionally requires an active Concept and a Current version. Ownership changes
invalidate the signed confirmation even for a Community admin; membership removal
revokes discard authority. Discard preserves ownership, grants, memberships and export
access. See [REF-155 report](ref-155-report.md).

## REF-162 Configure access placement

The product owner confirmed that ADR-011's current team ownership and independent
export access supersede REF-162's obsolete Viewer/Editor wording. Configure shows
the existing owner in Dataset details and user/team export-access controls in a
separate Access management tab. The tab shows who has access (owning team,
Community administrators, additional export access), the export grant form and the
additional grants with the date they were added. Grant and revoke post to
`/datasets/<key>/export-access` and return to the configuration page they came from
(`concept=1` or `standalone=1`); validation errors re-render that page. Inline refresh
replaces only the component. The former standalone access and owner panels
(`/datasets/<key>/export-access` GET, `/datasets/<key>/owner`) were removed (REF-165).
No legacy grant is reactivated.

Users and Teams show their relationship columns through one administrator-only
membership projection (one joined SELECT after the actor authorization SELECT).
Page query counts remain constant as memberships grow. Non-admin Teams keeps only
the actor's existing visible teams and exposes no membership roster or admin links.
