# Runtime health and operational logging

REF-70 provides deployment-neutral liveness, runtime readiness and structured
application logs. The probes contain no application data or diagnostic details and
are not part of the REST data API or its OpenAPI document.

## Probe contract

| Probe | Meaning | Success | Unavailable |
| --- | --- | --- | --- |
| `GET /healthz` | The running HTTP process can answer. It never opens a database connection. | `200 {"status":"ok"}` | Only normal process/request failure handling. |
| `GET /readyz` | The database answers the bounded check and `alembic_version` equals the single head shipped with this application. | `200 {"status":"ok"}` | `503 {"status":"unavailable"}`. |

Both responses are `application/json`, carry `Cache-Control: no-store` and return
an `X-Request-ID`. They require no browser session, CSRF token or API key. They
never return a DSN, configuration, exception, traceback, Dataset or Record value.
Readiness has a two-second response deadline and checks no external service.

Use `/healthz` for process replacement and `/readyz` for traffic admission. A
readiness `503` means that the running instance must not receive browser or API
traffic. Keep it running while investigating database connectivity and the applied
Alembic revision; do not use liveness to restart a process merely because its
database is temporarily unavailable.

Example reverse-proxy checks:

```text
liveness:  GET http://application:8000/healthz
readiness: GET http://application:8000/readyz
timeout:   2 seconds or longer at the caller
```

The host name above is illustrative. Keep actual topology, credentials and TLS
configuration in protected deployment configuration.

## Existing migration-before-start behavior

`refrncr start` retains its existing fail-closed contract. Before Uvicorn starts,
it creates the configured SQLite parent directory when needed, runs the existing
Alembic upgrade/preflights and bootstraps the Community installation. A failure in
that sequence prevents the HTTP process from starting; no probe is then available.

After a successful start, `/readyz` detects a later database outage or revision
change and returns `503`, while `/healthz` remains independent of the database.
[REF-168](https://cleverhorizons.atlassian.net/browse/REF-168) owns any future
separation of migration and server startup. REF-70 adds neither a migration command
nor a change to quick-start/package behavior.

## Request IDs

Every HTTP response, including browser, REST API, documentation, probe and safe
error responses, receives `X-Request-ID`. An inbound value is accepted only when
there is exactly one ASCII header value of 1–128 characters matching this grammar:

```text
[A-Za-z0-9][A-Za-z0-9._:-]{0,127}
```

Absent, duplicate, empty, oversized, non-ASCII, whitespace-containing or
control-character-containing values are replaced with a UUID. Request IDs belong
in the header, never in query parameters.

## JSON log contract

The normal CLI process writes one JSON object per stdout line. Request completion
events contain:

| Field | Meaning |
| --- | --- |
| `timestamp` | UTC RFC 3339 timestamp with `Z`. |
| `severity` | `INFO`, `WARNING`, `ERROR` or the configured threshold level. |
| `event` | Stable event name such as `http.request_completed`. |
| `request_id` | Accepted or generated correlation ID. |
| `http_method` | HTTP method. |
| `route` | Registered route template or a fixed mounted-browser template, never the raw target. |
| `status_code` | Final HTTP status. |
| `duration_ms` | Monotonic elapsed duration in milliseconds. |

Startup/shutdown use `application.starting`, `application.started` and
`application.stopped`. Failed readiness emits `health.readiness_failed`. Normal
completed requests are `INFO`; rate limits and failed readiness are `WARNING`;
unexpected failures and other 5xx responses are `ERROR` with only a safe category.

Set the minimum severity with `REFRNCR_LOG_LEVEL`. Accepted case-insensitive values
are `DEBUG`, `INFO`, `WARNING`, `ERROR` and `CRITICAL`; the default is `INFO`.
Whitespace around a valid value is ignored. An invalid value safely falls back to
`INFO` and emits `logging.configuration_invalid` without copying the supplied value.

Operational logs never contain request/response bodies, cookies, Authorization or
API-key headers, raw paths/query strings, search terms, imports, email addresses,
Dataset/Record values, DSNs, secrets, raw exception messages or production
tracebacks. Configure reverse proxies and hosting platforms to apply the same
boundary to their independent logs.

Operational logging is transient process diagnostics. It neither replaces nor
duplicates the durable domain audit trail, whose contracts remain documented in
[the audit policy](audit-policy.md).
