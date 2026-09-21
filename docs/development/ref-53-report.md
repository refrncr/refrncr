# REF-53 implementation report

## Outcome and Community boundary

REF-53 documents and verifies operator-managed recovery for REFRNCR Community's
embedded SQLite and customer-managed PostgreSQL profiles. REFRNCR does not schedule,
store, transport, encrypt, retain or centrally manage backup artifacts. Operators
remain responsible for execution, scheduling, storage, encryption technology, key
management, access provisioning, retention and disposal.

No backup service, scheduler, background worker, cloud/storage integration,
credential, Docker application runtime, runtime dependency, database migration or
change to `refrncr start` was added. Existing fail-closed automatic migration and
bootstrap remain intact; REF-168 still owns any future separation of migration and
serving.

## Supported scope and exclusions

The supported backup unit is the complete database: Datasets, Fields, Records,
Concepts, Versions, users, teams, memberships/grants and other access-management
data, API-key records, and audit history. Staged import uploads and other TTL-bound
transient data are excluded and cannot be restored. A database backup is not a backup
of configuration or secrets.

The durable guide is
[`backup-recovery.md`](backup-recovery.md). It specifies:

- at least daily backups, at least 30 days retention and a restore exercise at least
  quarterly;
- encryption at rest and access limited to explicitly authorised operators;
- a secret-free recovery manifest containing REFRNCR/database versions, backup date,
  protected location reference, method, required configuration-variable names,
  managed-secret reference, validation, cut-over and rollback steps;
- transactionally consistent SQLite backup/restore through Python's supported
  `sqlite3.Connection.backup` API plus `PRAGMA integrity_check`;
- PostgreSQL custom-format logical backup/restore through matching-major `pg_dump`
  and `pg_restore`, archive/catalogue checks and a new `template0` target;
- validation through the unchanged fail-closed `refrncr start` path;
- controlled cut-over only after data, Concept, audit, authorization, Alembic and
  readiness validation;
- rollback by selecting the unchanged original database.

Every example uses explicit placeholders. Secret values are excluded from commands,
documentation, fixtures, tool errors and this report. PostgreSQL client tools,
database administration capability, protected storage and encryption are operator
prerequisites, not application dependencies.

## Verification design

`tests/test_recovery.py` creates only synthetic users, access membership, Dataset
metadata, Fields, a Record, a Current published Version, an active Concept and audit
events. For both engines it:

1. creates and populates a disposable source;
2. snapshots source counts;
3. creates a consistent backup using the documented mechanism;
4. restores into a newly created empty target;
5. runs the existing `refrncr start` composition with only the Uvicorn network bind
   replaced by an in-process recorder;
6. verifies the Alembic head, published and Concept values, audit count and preserved
   maintainer/outsider authorization boundary;
7. proves source counts did not change;
8. exercises a failed restore and checks that the source remains unchanged and the
   returned error is actionable and contains no configured password.

The PostgreSQL fixture creates and drops randomly named disposable databases. It
requires configured PostgreSQL integration testing and fails if `pg_dump` or
`pg_restore` is absent. It explicitly asserts a PostgreSQL 16 server and a matching
major-16 `pg_dump`; it introduces no skip beyond the repository's existing convention
when `REFRNCR_TEST_POSTGRES_URL` is not configured.

## Test environment and results

Environment used on 2026-09-21:

- macOS developer host, Python 3.12.2;
- SQLite library/CLI 3.51.0;
- PostgreSQL server 16.15 in a disposable local test container;
- `pg_dump` 16.15 and `pg_restore` 16.15 installed as operator/test tooling;
- SQLAlchemy 2.0.52, Alembic 1.19.1 and psycopg 3.3.4;
- package virtual environment: repository `.venv`.

Final successful checks:

| Command | Result |
| --- | --- |
| `.venv/bin/python -m ruff check .` | Passed. |
| `.venv/bin/python -m ruff format --check .` | 227 files already formatted. |
| `.venv/bin/python -m pre_commit run --all-files` | All configured hooks passed. |
| `git diff --check` | Passed. |
| `PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH REFRNCR_TEST_POSTGRES_URL=<disposable PostgreSQL 16 URL> .venv/bin/python -m pytest -q tests/test_recovery.py --tb=short` | 4 passed in 3.58 s. |
| `PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH REFRNCR_TEST_POSTGRES_URL=<disposable PostgreSQL 16 URL> .venv/bin/python -m pytest -q --tb=short` | 3,875 passed; 3 existing warnings; 865.36 s. |
| `env -u REFRNCR_TEST_POSTGRES_URL .venv/bin/python -m pytest -q --tb=short --deselect=tests/test_database.py::test_postgresql_database_connection` | 2,705 passed, 1,169 existing engine-specific skips, 1 PostgreSQL-only test deselected; 3 existing warnings; 359.38 s. |

The complete PostgreSQL run covers database/migration/CLI/package tests and the audit,
access, API-key, Concept/version, import and published-data regressions. The complete
SQLite run covers the same repository suite with PostgreSQL parametrisations skipped
by their existing fixtures and the unconditional PostgreSQL connection smoke test
explicitly deselected. Package/wheel and live CLI-start tests passed in both final
runs.

An initial sandboxed all-SQLite attempt produced 2,703 passes but failed only because
the sandbox prohibited two loopback port binds and the unconditional PostgreSQL smoke
test attempted its default `localhost:5432`. The final run was repeated outside that
network sandbox; no application assertion, skip or test implementation was weakened.

## Impact review

- Authorization: unchanged; restored membership-based Concept access and outsider
  non-disclosure are verified.
- Tenant isolation: no change; Community remains a single-organisation installation.
- Database schema/migrations: no change and no migration added.
- API contracts: no change.
- Security: operator ownership, encryption/access baseline and secret-exclusion rules
  are made explicit; no secret material was added.
- Dependencies: no project dependency changed. PostgreSQL client tooling remains an
  external operator/test prerequisite.
- Architecture: no new application architecture or ADR; this is an operational
  procedure using existing database and start contracts.
- Documentation: README and operations documentation link the new standalone guide.

## Changed files

- `README.md`
- `docs/development/operations.md`
- `docs/development/backup-recovery.md`
- `docs/development/ref-53-report.md`
- `tests/test_recovery.py`

## Remaining limitations and operator responsibilities

- Local success is not the authoritative CI result. GitHub Actions must pass before
  merge.
- REFRNCR cannot guarantee the availability, durability or confidentiality of
  operator storage and cannot recover lost encryption/database/API-pepper secrets.
- Operators must select and monitor storage, encryption, key custody, access,
  retention, capacity and restore frequency appropriate to their RPO/RTO.
- PostgreSQL roles need enough separately governed privilege to dump the complete
  source and provision/restore an empty target. The guide deliberately does not
  prescribe a provider or embed credentials.
- Cut-over remains an operator-controlled deployment change. The tests verify the
  database and fail-closed application composition but do not modify a live routing
  or database selector.
- TTL-bound transient imports must be restarted from their original source after
  recovery.

## Explicit staging list

If local review approves the change, stage exactly:

```shell
git add README.md \
  docs/development/operations.md \
  docs/development/backup-recovery.md \
  docs/development/ref-53-report.md \
  tests/test_recovery.py
```

Recommended commit message:

```text
docs(ops): verify operator backup and recovery procedures (REF-53)
```

Recommended push command after an operator creates the commit:

```shell
git push -u origin feat/ref-53-operator-recovery
```

Nothing was staged, committed or pushed during implementation.
