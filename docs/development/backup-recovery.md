# Operator backup and recovery guide

This guide is the supported recovery procedure for REFRNCR Community. Community
supports embedded SQLite and customer-managed PostgreSQL. REFRNCR documents and
tests recovery of those databases; it does not run a backup service.

## Responsibility and recovery boundary

The operator owns backup execution and scheduling, the storage location, transport,
encryption, key management, access provisioning, retention and deletion. Production
backup artifacts must be encrypted at rest and accessible only to explicitly
authorised operators. Keep encryption keys and database credentials in managed secret
storage, separate from both the backup and the recovery manifest.

Use at least this production baseline unless stricter RPO/RTO requirements apply:

- take a complete database backup every day;
- retain backups for at least 30 days;
- perform and record a restore exercise at least quarterly.

The supported backup unit is the complete database. It consistently contains
Datasets, Fields, Records, Concepts, Versions, access-management data, API-key records
and audit history. A database backup is not a configuration or secret backup. Staged
import uploads and other TTL-bound transient data are deliberately excluded and cannot
be recovered.

Never restore over a live production database. Restore into a newly created, empty
target, validate it while production remains unchanged, and only then perform a
controlled cut-over. Keeping the original database intact is the rollback path.

## Secret-free recovery manifest

Store this checklist beside operational recovery records, without secret values:

```text
REFRNCR version: <installed package version>
Database engine/version: <SQLite version or PostgreSQL server major/minor>
Backup date (UTC): <timestamp>
Backup location: <protected storage reference, not credentials>
Backup method: <Python sqlite3 backup API or pg_dump custom archive>
Required configuration-variable names: REFRNCR_DATABASE_URL, REFRNCR_API_KEY_PEPPER,
  REFRNCR_HOST, REFRNCR_PORT, REFRNCR_TRUSTED_PROXIES, REFRNCR_LOG_LEVEL
Managed secret reference: <vault/path or operator-controlled reference, never the value>
Validation: <integrity, Alembic, application and authorization checks below>
Cut-over: <approved change record and target selector>
Rollback: <original database selector and decision owner>
```

Do not put passwords, API keys, pepper values, encryption keys, DSNs containing
credentials, real Record contents or backup contents in repository files,
documentation, tests, logs or recovery reports.

## Common preparation

1. Announce the exercise and prevent writers during the final backup/cut-over window.
2. Record the manifest fields above and the installed REFRNCR version.
3. Provision a protected backup location and a separate, new recovery target.
4. Retrieve required secrets directly from managed secret storage into the process
   environment. Do not paste them into commands or logs.
5. Keep production serving from the original database throughout restore and
   validation.

All uppercase shell variables below are placeholders chosen by the operator. The
example values are deliberately non-production paths and names. Do not copy actual
hosts, users, paths or secrets into this repository.

## SQLite procedure

Prerequisites: the Python version running REFRNCR (with its standard `sqlite3`
module), read access to the source database and a private destination directory. The
Python `sqlite3.Connection.backup` API creates a transactionally consistent snapshot
even when the source is in WAL mode. Do not copy only the `.db` file with a generic
filesystem copy while writers are active.

Set placeholders and refuse permissive destination access:

```shell
export SOURCE_DATABASE=/srv/refrncr/refrncr.db
export BACKUP_FILE=/secure/refrncr/refrncr-YYYYMMDD.db
export RESTORE_DATABASE=/srv/refrncr-recovery/refrncr.db
install -d -m 0700 "$(dirname "$BACKUP_FILE")" "$(dirname "$RESTORE_DATABASE")"
test -f "$SOURCE_DATABASE"
test ! -e "$BACKUP_FILE"
test ! -e "$RESTORE_DATABASE"
```

Create the consistent backup. The script opens the source read-only and refuses an
existing destination:

```shell
python - "$SOURCE_DATABASE" "$BACKUP_FILE" <<'PY'
import pathlib
import sqlite3
import sys
import urllib.parse

source, destination = map(pathlib.Path, sys.argv[1:])
if destination.exists():
    raise SystemExit("backup destination must not exist")
uri = "file:" + urllib.parse.quote(str(source.resolve())) + "?mode=ro"
with sqlite3.connect(uri, uri=True) as origin, sqlite3.connect(destination) as backup:
    origin.backup(backup)
PY
chmod 0600 "$BACKUP_FILE"
```

Check the backup before it leaves the protected host:

```shell
python - "$BACKUP_FILE" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as database:
    result = database.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    raise SystemExit("backup integrity check failed")
print("backup integrity: ok")
PY
```

Restore the verified backup into a new database, using the backup API again rather
than overwriting an existing file:

```shell
python - "$BACKUP_FILE" "$RESTORE_DATABASE" <<'PY'
import pathlib
import sqlite3
import sys

source, destination = map(pathlib.Path, sys.argv[1:])
if destination.exists():
    raise SystemExit("restore target must be new and empty")
with sqlite3.connect(source) as backup, sqlite3.connect(destination) as restored:
    backup.backup(restored)
PY
chmod 0600 "$RESTORE_DATABASE"
```

Repeat `PRAGMA integrity_check` against `RESTORE_DATABASE`, then follow the shared
validation section. A failed backup, integrity check, restore or application start
does not justify modifying the source. Remove or quarantine only the failed disposable
target and retry from a verified backup. Rollback is selection of the unchanged
original database.

## PostgreSQL procedure

Prerequisites: customer-managed PostgreSQL, protected backup storage and PostgreSQL
client tools supplied by the operator. Use `pg_dump`, `pg_restore`, `createdb` and
`psql` from the same major release as the source server (PostgreSQL 16 for the tested
profile). `pg_dump` cannot dump a server newer than its own major version. Before a
production restore, confirm the archive is supported by the target's `pg_restore`;
use a target server on the same PostgreSQL major for the lowest-risk exercise.
PostgreSQL tooling and storage are operator prerequisites, not REFRNCR runtime
dependencies.

Keep credentials out of arguments and URLs. Configure a protected `.pgpass`, a
short-lived `PGPASSFILE`, or an equivalent operator-managed credential mechanism.
The following variables contain only placeholders:

```shell
export SOURCE_HOST=db-source.example.invalid
export SOURCE_PORT=5432
export SOURCE_DATABASE=refrncr
export SOURCE_USER=refrncr_backup
export BACKUP_FILE=/secure/refrncr/refrncr-YYYYMMDD.dump
export TARGET_HOST=db-recovery.example.invalid
export TARGET_PORT=5432
export TARGET_DATABASE=refrncr_recovery_YYYYMMDD
export TARGET_USER=refrncr_restore
test ! -e "$BACKUP_FILE"
```

Verify client/server compatibility, create a logical custom-format backup and inspect
its archive catalogue:

```shell
pg_dump --version
psql --host "$SOURCE_HOST" --port "$SOURCE_PORT" --username "$SOURCE_USER" \
  --dbname "$SOURCE_DATABASE" --tuples-only --no-align \
  --command "SHOW server_version;"
pg_dump --host "$SOURCE_HOST" --port "$SOURCE_PORT" --username "$SOURCE_USER" \
  --format=custom --file "$BACKUP_FILE" "$SOURCE_DATABASE"
chmod 0600 "$BACKUP_FILE"
pg_restore --list "$BACKUP_FILE" >/dev/null
```

Provision a new empty target. `createdb` must fail if the name already exists; do not
add `--if-exists`, `--clean` or a command that drops a database:

```shell
createdb --host "$TARGET_HOST" --port "$TARGET_PORT" --username "$TARGET_USER" \
  --template template0 "$TARGET_DATABASE"
pg_restore --host "$TARGET_HOST" --port "$TARGET_PORT" --username "$TARGET_USER" \
  --dbname "$TARGET_DATABASE" --exit-on-error --no-owner --no-privileges "$BACKUP_FILE"
```

Confirm the restored database is structurally readable before application validation:

```shell
psql --host "$TARGET_HOST" --port "$TARGET_PORT" --username "$TARGET_USER" \
  --dbname "$TARGET_DATABASE" --set ON_ERROR_STOP=1 \
  --command "SELECT version_num FROM alembic_version;" \
  --command "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';"
```

Treat any non-zero tool exit as failure. Keep tool output in protected operator logs
and redact topology; never echo credential variables or a credential-bearing DSN. On
failure, keep production on the source, quarantine or drop only the disposable target
through the approved operator process, correct permissions/capacity/version mismatch,
and restore again. The unchanged source database remains the rollback target.

## Validation before cut-over

Configure a separate validation process to use the restored target. With SQLite use
`refrncr start --database-path "$RESTORE_DATABASE"`; with PostgreSQL inject the
credential-bearing `REFRNCR_DATABASE_URL` from managed secret storage and run
`refrncr start`. This intentionally exercises the existing fail-closed automatic
Alembic/preflight/bootstrap path. REF-168, not this procedure, owns any future
separation of migration and serving.

Record evidence for every check:

1. The restore target is new and the original database has not changed.
2. The integrity check/catalogue inspection passed and `alembic_version` is compatible
   with the installed REFRNCR version.
3. `refrncr start` succeeds; `/healthz` and `/readyz` return `200`.
4. Representative Current published Dataset metadata, Fields and Records are readable.
5. An authorised maintainer can see a Concept while an ordinary user cannot see its
   unpublished values.
6. Version history, users, teams, memberships/grants, API-key records and audit history
   have the expected counts and representative entries.
7. Existing API keys are checked only through normal authentication; never print key
   material. The same managed `REFRNCR_API_KEY_PEPPER` is required for existing key
   records to remain usable.
8. The operator records timestamps, versions, outcomes and approval without including
   secrets or real Record values.

Stop the validation process after success. During an approved maintenance window,
stop writers, take a final backup if the exercise is becoming a real recovery, repeat
the restore and validation, then change the deployment's database selector to the
validated target. Do not rename, delete or overwrite the original database during
cut-over. Monitor readiness, authentication, authorisation, audit writes and Current
reads. If validation or monitoring fails, point the deployment back to the intact
original database and investigate the disposable target.

## Troubleshooting and escalation

- Integrity failure: quarantine the artifact and create a new consistent backup.
- `pg_dump` reports a server-version mismatch: install the matching PostgreSQL client
  major; do not work around the compatibility check.
- Restore reports existing objects: the target was not empty. Select a new target.
- `refrncr start` fails at migration/preflight: keep production unchanged and follow
  the emitted secret-free error plus the relevant upgrade guide. Do not edit the
  restored schema manually.
- Missing transient import state is expected; restart the import from its source.
- Missing durable data, unexpected authorisation, audit inconsistency or unusable API
  keys means validation failed. Do not cut over; retain evidence and escalate through
  the operator's incident/security process.

REFRNCR cannot recover an absent/corrupt artifact, retrieve operator-managed keys,
choose a storage provider or certify infrastructure access controls. Those remain the
operator's responsibility.
