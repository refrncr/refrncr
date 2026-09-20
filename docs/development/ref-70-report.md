# REF-70 implementation report

## Scope and decisions

REF-70 adds public process liveness, runtime database/Alembic readiness and
privacy-safe operational JSON logging to the existing FastAPI root. The mounted
Flask browser application, `/api/v1`, `/api/docs`, `/api/openapi.json`, durable
audit semantics and database schema remain unchanged.

The product owner separated the material deployment-contract change into
[REF-168](https://cleverhorizons.atlassian.net/browse/REF-168). `refrncr start`
therefore keeps its existing fail-closed sequence: automatic migration/preflight
and Community bootstrap must succeed before Uvicorn starts. REF-70 adds no migration,
migration command or startup/migration separation.

## Changed files

| File | Change |
| --- | --- |
| `src/refrncr/operational.py` | Request-ID validation, safe JSON formatter/configuration, ASGI completion middleware, readiness deadline and Alembic-head check. |
| `src/refrncr/app.py` | Root probes, operational middleware/lifecycle events and existing-engine readiness injection; retained `/api/health` compatibility route. |
| `src/refrncr/cli.py` | Configure JSON stdout logging and disable duplicate raw Uvicorn access output after the unchanged migration/bootstrap sequence. |
| `src/refrncr/audit.py` | Clarify the deliberately non-audited operational-probe boundary. |
| `src/refrncr/templates/pages/api/docs.html` | Non-sensitive operations-guide reference outside Swagger/OpenAPI. |
| `tests/test_operational.py` | SQLite/PostgreSQL probe, request-ID, timeout, logging, redaction, severity and error tests. |
| `tests/test_cli.py` | Structured CLI logging configuration regression. |
| `tests/test_packaged_cli.py` | Live installed-wheel probes, JSON-log evidence and safe bind-failure category. |
| `tests/test_api_documentation.py` | Operations reference, probe exclusion and public README traceability-boundary assertions. |
| `docs/development/operations.md` | Deployment/probe/logging/redaction/operator guide. |
| `docs/development/rest-api.md` | Route-template logging boundary and operational-doc reference. |
| `docs/development/audit-inventory.json` | Explicit `/healthz`, `/readyz` and retained `/api/health` interface inventory. |
| `docs/development/audit-coverage.json` | Non-domain operational probe decision and testable interface mapping. |
| `docs/architecture/adr-009-flask-fastapi-composition.md` | REF-70 ASGI operational boundary and REF-168 dependency. |
| `README.md` | User-facing probe, log-level and unchanged fail-closed quick-start summary without internal Jira references. |

## Endpoint contract

`GET /healthz` returns exactly `200 {"status":"ok"}` and never invokes the
readiness/database operation. `GET /readyz` runs `SELECT 1` and reads
`alembic_version` using the already composed SQLAlchemy engine. The expected
revision comes from the single head in the packaged Alembic script directory;
there is no duplicated revision constant or database configuration.

Readiness succeeds only when both operations complete and the revisions match.
Connection errors, a missing version table/row, revision mismatch, timeout and
unexpected exceptions become exactly `503 {"status":"unavailable"}`. The async
response deadline is two seconds. Both probes always use JSON, `Cache-Control:
no-store` and `X-Request-ID`, require no credential/session/CSRF, and create no
domain audit event.

Under the retained CLI contract, a migration problem found before Uvicorn starts
still prevents the process and therefore exposes no probes. REF-168 owns changing
that behavior. Once a process has started successfully, a later database outage or
revision change makes readiness unavailable without affecting liveness.

## Operational log contract

The normal CLI runtime writes one JSON object per stdout line. Request completion
uses UTC `timestamp`, `severity`, stable `event`, `request_id`, `http_method`,
registered/mounted `route` template, `status_code` and monotonic `duration_ms`.
Failed readiness and 429 are `WARNING`; unexpected exceptions and other 5xx are
`ERROR`; ordinary completions and lifecycle events are `INFO`. Safe runtime error
categories include `address_in_use`; raw Uvicorn exception messages are not copied.

`REFRNCR_LOG_LEVEL` accepts `DEBUG`, `INFO`, `WARNING`, `ERROR` and `CRITICAL`
case-insensitively, defaults to `INFO`, and falls back to `INFO` with the stable
`logging.configuration_invalid` warning when invalid. The supplied invalid value is
not logged.

Exactly one inbound ASCII request ID matching
`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` is retained. Missing, empty, duplicate,
oversized, non-ASCII, whitespace/control-containing IDs are replaced with a UUID.
The selected ID is returned on probe, browser, API, documentation and error
responses and is available to request-scoped formatter events.

## Privacy and redaction evidence

The formatter serializes an allowlist and never the `LogRecord` message,
arguments, exception object or traceback. Tests inject cookie/Authorization/API-key
labels, search/import text, an email address, Dataset/Record labels, DSN and secret
into messages and exception state and prove none appears in serialized output.
HTTP tests use sensitive-looking path and query values and prove logs contain only
`/api/v1/datasets/{dataset_key}` or the fixed `/{browser_path}` mount template.
No request/response body or header collection is passed to logging.

Operational events remain stdout diagnostics. The existing persistent audit catalog,
inventory and coverage guard explicitly classify probes as non-domain operations;
probe tests verify that audit row counts do not change.

## Verification environment

* Python: 3.12.2 (`.venv`).
* SQLite: 3.51.0.
* PostgreSQL: 16.15 (`postgres:16`, disposable isolated database/schema on loopback).
* Package evidence: wheel and sdist built by the existing fixture, wheel installed
  into an isolated virtual environment, and the installed `refrncr` executable
  started on a real loopback port.

## Commands and results

| Command/profile | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_operational.py -q` with PostgreSQL 16 configured | 38 passed; 1 existing TestClient deprecation warning. |
| `.venv/bin/python -m pytest tests/test_api_documentation.py -q` after the public README correction | 5 passed, 3 PostgreSQL-profile skips; 1 existing warning. |
| Focused audit/CLI/probe/docs/API regression selection | 179 passed, 66 PostgreSQL-profile skips while the variable was unset; 1 existing warning. |
| Installed wheel live HTTP startup test | 1 passed; `/healthz` and `/readyz` exact responses and JSON stdout verified. |
| Installed wheel startup plus real port-conflict tests | 2 passed; safe `address_in_use` category and no database-path leak. |
| Final full SQLite profile after the public README correction, PostgreSQL-only connection case deselected | 2,685 passed, 1,167 PostgreSQL-profile skips, 1 deselected, 3 existing warnings, 371.95 s. |
| Final full PostgreSQL 16.15-enabled suite (including SQLite parametrizations) | 3,852 passed, no skips, 3 existing warnings, 962.05 s. |

| `.venv/bin/python -m ruff check .` | Passed. |
| `.venv/bin/python -m ruff format --check .` | Passed; 218 files already formatted. |
| `.venv/bin/python -m pre_commit run --all-files` | Passed all configured hooks. |
| `git diff --check` | Passed. |

GitHub Actions remains the authoritative independent pre-merge gate.

After final review, every internal `REF-xx` reference was removed from the public
README, including references outside the newly added operational section. A focused
documentation regression now protects that user-facing boundary. Traceability remains
in Jira, ADRs, this development report and future pull-request context.

## Performance findings

Liveness performs no persistence work. Readiness performs one connection checkout,
one `SELECT 1` and one scalar Alembic-revision query in a worker thread, bounded by
the two-second response deadline so it does not block the ASGI event loop. Request
duration uses `time.perf_counter()` and logging performs one compact JSON
serialization per event. No throughput/load benchmark or production latency claim
is made.

A timed-out synchronous driver call can continue in its worker thread until the
driver/operating system releases it even though the HTTP response deadline has
expired. Deployments should retain driver-level connection/query timeouts. A future
change may add dialect-specific cancellation only with an explicit cross-database
contract; REF-70 adds no dependency or duplicate engine.

## Impact review and remaining risks

* Authorization/tenant isolation: unchanged; probes are intentionally public and
  data-free. Community remains installation-scoped.
* Database/migrations: no schema or migration; existing engine and Alembic history
  are reused. `refrncr start` remains fail-closed.
* API: two root operational endpoints and universal `X-Request-ID`; REST data bodies,
  status contracts and OpenAPI paths are unchanged.
* Security/privacy: allowlisted structured output and route templates reduce leakage;
  independent proxy/platform logs remain operator-controlled.
* Dependencies: none added.
* Audit: no probe event and no replacement/duplication of domain audit.
* Architecture: additive middleware/probes at the established FastAPI root.
* Residual deployment gap: liveness during a startup-time migration failure remains
  impossible under the existing contract and is explicitly tracked by REF-168.
* CI/release: local evidence does not replace GitHub Actions or release validation.

## Explicit staging list

```text
README.md
docs/architecture/adr-009-flask-fastapi-composition.md
docs/development/audit-coverage.json
docs/development/audit-inventory.json
docs/development/operations.md
docs/development/ref-70-report.md
docs/development/rest-api.md
src/refrncr/app.py
src/refrncr/audit.py
src/refrncr/cli.py
src/refrncr/operational.py
src/refrncr/templates/pages/api/docs.html
tests/test_api_documentation.py
tests/test_cli.py
tests/test_operational.py
tests/test_packaged_cli.py
```

Recommended commit message:

```text
feat(ops): add health checks and structured logs (REF-70)
```

Recommended push command after local review and commit:

```shell
git push -u origin feat/ref-70-health-and-operational-logs
```
