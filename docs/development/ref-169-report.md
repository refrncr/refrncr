# REF-169 — API-key success logging and recent use

## Product decisions

Successful API-key authentication no longer creates a durable
`api_key.authenticated` event. The existing `http.request_completed` event receives only
the fixed `authentication: "api_key"` marker after success. This does not introduce usage
statistics, identifying telemetry, dashboards, counters, IP storage, SIEM export or alerts.

`last_used_at` remains an approximate indication of recent successful use. Authentication
locks the key, never its owner, and executes one conditional update when the stored value is
absent or older than 15 minutes. The exact 15-minute boundary is not updated; a later instant
is. Successful 304 responses qualify. Rejected credentials never update the value.

## Related-story impact

- REF-40: creation, revocation and failed-authentication audits remain; successful
  authentication no longer creates a durable event or takes the owner-row lock.
- REF-107: all Current API routes, authorization and error contracts are unchanged; 304
  authentication participates in the throttled recent-use update.
- REF-31: durable failed, expired, malformed and revoked-key outcomes remain unchanged.
- REF-70 and REF-170: the existing completion event is enriched; no separate success event is
  emitted, and interactive request suppression/aggregation remains unchanged.
- REF-149: the audit catalog and coverage matrix no longer require a new success event per
  request. Lifecycle and failed-authentication coverage remain explicit.

REF-31, REF-40 and REF-107 are Community/core capabilities, so this change belongs in this
repository. There is no Cloud-only orchestration, tenancy or billing impact.

## Historical compatibility and privacy evidence

The `api_key.authenticated` event contract and presentation mapping remain in place.
Existing rows therefore keep their action label, key UUID and resolved historical owner;
there is no migration, rewrite or hiding of audit history.

The operational formatter allowlists `authentication` alongside the existing method,
redacted route template, status, duration and request ID. Tests assert that successful log
output omits the credential, key UUID, owner/user UUID, Authorization header and key name.
Request queries, bodies and Dataset/Record values never enter this event.

## Concurrency, failure and rollback

PostgreSQL key-row locking preserves ordering with revocation without locking the owner.
The subsequent SQL `UPDATE` contains the cutoff predicate, so concurrent requests can cause
at most one timestamp change and losers continue normally. SQLite retains its existing
serialized-write unit of work. A persistence error rolls back the transaction and fails
authentication closed. No retry is introduced. Application rollback requires only restoring
the previous code; the unchanged schema and historical event contract need no data rollback.

## Validation and staging

The explicit staging list for REF-169 is every modified or untracked source, test and
documentation file reported by `git status --short`; no files are staged by this task.

- Focused API-key, REST, operational, catalog and history selection: 232 passed, 137 skipped.
- Full SQLite selection: 2722 passed, 1176 PostgreSQL cases deselected, three warnings.
- Full PostgreSQL 16.15 selection: 3896 passed and two recovery setup errors caused solely
  by the shell selecting `pg_dump` 15; both recovery tests then passed with the installed
  `pg_dump`/`pg_restore` 16.15 selected in `PATH`.
- Ruff lint and format checks, all configured pre-commit hooks and `git diff --check`: passed.

The first sandboxed SQLite run also reached 2720 passes before two packaged-CLI tests were
denied loopback socket binding; the complete rerun outside that restriction passed all 2722.
GitHub Actions remains the authoritative independent gate.

## Remaining limitations

`last_used_at` intentionally cannot reconstruct request counts or exact usage times. This
story adds no capacity reporting or per-key usage telemetry. Independent proxies and hosting
platforms must continue to enforce their own equivalent redaction policy.
