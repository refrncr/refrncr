# REF-170 implementation report

## Decisions and REF-70 boundary

REF-170 adds a human terminal presentation above REF-70's existing allowlisted operational
events. Interactive stdout is selected only with `isatty()`; redirected and collected stdout
retains one JSON object per line. The same event builder supplies both renderers. The CLI option
`--log-level` is validated without reflecting an invalid value and overrides
`REFRNCR_LOG_LEVEL` only for the launched process.

The existing fail-closed migration/bootstrap sequence, FastAPI/Flask composition, probes,
REST/OpenAPI, authentication, authorization and durable audit trail are unchanged. There is no
database schema change, migration, dependency or architecture decision.

## Presentation and redaction evidence

The ready line is emitted from the existing `application.started` lifespan event, so it occurs
once and only after preparation and server startup succeed. TTY INFO request noise is suppressed;
interactive DEBUG shows only method, route template/category, status, duration, request ID and a
fixed error category. Consecutive equivalent debug requests are counted and summarized, while
warnings and errors flush the summary and remain visible.

The terminal handler consumes the same allowlisted dictionary as the JSON formatter. Neither
renderer copies request/response bodies, query strings, raw identifier paths, headers, search or
domain values, connection strings, email addresses, raw exceptions or tracebacks. Startup errors
use fixed cause and next-step text. `NO_COLOR` disables ANSI sequences; plain text retains the
complete meaning. `0.0.0.0` is represented by a `localhost` browser URL plus an explicit
all-interface binding line, without IP discovery.

## Verification

| Command/profile | Result |
| --- | --- |
| Final focused CLI, operational, package and live installed-CLI tests | 84 passed, 14 PostgreSQL-profile skips, 1 existing warning; 16.17 s. |
| Final full SQLite profile | 2,718 passed, 1,171 PostgreSQL-profile skips, 1 PostgreSQL-only test deselected, 3 existing warnings; 358.46 s. |
| Final full PostgreSQL 16.15 profile | 3,890 passed, no skips, 3 existing warnings; 1,000.85 s. |
| `.venv/bin/python -m ruff check .` | Passed. |
| `.venv/bin/python -m ruff format --check .` | Passed; 231 files already formatted. |
| `.venv/bin/python -m pre_commit run --all-files` | Passed all configured hooks. |
| `git diff --check` | Passed. |

PostgreSQL used a disposable `postgres:16` container bound only to loopback and the installed
PostgreSQL 16.15 client tools. The container was stopped and automatically removed after the run.
The initial PostgreSQL attempt exposed a local client/server major mismatch (client 15 versus
server 16); after correcting `PATH`, the complete suite was rerun successfully. GitHub Actions
remains the authoritative independent pre-merge gate.

## Impact and limitations

- Authorization, tenant isolation, audit, API contracts and database schema/migrations: unchanged.
- Security: output remains allowlisted; external reverse-proxy/platform logs remain operator-owned.
- Dependencies and architecture: unchanged.
- Aggregation is process-local and consecutive-event based; it is intentionally not metrics or
  durable telemetry.
- A pre-lifespan bind failure cannot emit the ready line. Uvicorn's fixed safe runtime category is
  rendered with a practical port-conflict next step.

## Explicit staging list

```text
README.md
docs/development/operations.md
docs/development/ref-170-report.md
src/refrncr/cli.py
src/refrncr/operational.py
tests/test_cli.py
tests/test_operational.py
tests/test_packaged_cli.py
```

Recommended commit message:

```text
feat(cli): improve interactive startup output (REF-170)
```
