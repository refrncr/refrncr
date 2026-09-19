# REF-107 — Current published data REST API

Status: technically ready for local review on `feat/ref-107-rest-read-api`; nothing staged,
committed or pushed. Base: current main `38610a0aef2c356445d39312062ed44ce104129f`.
Independent GitHub Actions CI cannot run for this unpushed change and remains the
pre-merge gate. This is not a release or deployment claim.

## Scope and source review

Read repository AGENTS.md, REF-107, REF-40, REF-109, ADR-009 through the relevant
persistence/governance/datatype/version/metadata/API-key decisions, current security,
audit, import/export/version guidance, existing ASGI composition, services, ports,
authorization and tests. Confluence API Architecture & Effective Dating, Architecture
Overview & Domain Model and Security Architecture informed the implementation.

The product owner explicitly corrected the browser-composition premise: keep Flask,
FastAPI ASGI entrypoint and the existing a2wsgi root mount. Confluence's approved
target already records that correction. The implementation is Community/core; no
Cloud-only infrastructure, tenant schema, alternative service or permission model.
Local ADR-019 records the implementation decisions without claiming deployment.

## Contract and endpoint matrix

| GET endpoint | Response | Parameters |
| --- | --- | --- |
| `/api/v1/datasets` | `data: Dataset[]`, `pagination`, `links` | offset/limit |
| `/api/v1/datasets/{dataset_key}` | `data: DatasetDetail`, including ordered Fields | None |
| `/api/v1/datasets/{dataset_key}/records` | `data: Record[]`, `current_version`, pagination/links | offset/limit |
| `/api/v1/datasets/{dataset_key}/records/{identifier}` | `data: Record`, `current_version` | None |

Pydantic models govern JSON. Dataset summaries include key/name/description/icon
and Current number/publication timestamp. Fields include key/name/datatype,
identifier/required/unique flags, display order, definition/unit. Records contain
only their opaque canonical business identifier and canonical values under Field
keys; absent values are null. Exact Decimal JSON numbers avoid float conversion.
No internal Record UUID or per-Record Field metadata is returned.

Identifiers are unpadded base64url of the **complete existing** length-prefixed
canonical identifier bytes, capped at the existing 2,000-byte limit. This is only
reversible transport encoding, shared by single/composite identifiers. Clients URL
encode the complete returned value. It is neither secrecy nor authorization.

Collections default to offset 0/limit 50, maximum limit 100; offset is bounded by
2147483647. Duplicate/unknown/noncanonical query values fail closed. Dataset order
is key ascending; Records retain existing typed identifier order. Safe relative
links include only validated path keys and pagination numbers. No Current means
404/absent listing, never a Concept/Scheduled/Expired/Cancelled fallback. No writes,
search, filters, alternate sort, dates/history, import/export, mapping or hierarchy.
Public OpenAPI/Swagger delivery remains disabled for REF-109; source schema generation
works programmatically. See [complete contract](rest-api.md).

## Authentication and authorization

REF-40's existing `ApiKeyService.authenticate` alone verifies exactly one Bearer
header. The owner is reauthorized using the existing Dataset authorization layer
inside the existing read snapshot. Missing/malformed/duplicate/unknown/expired/
revoked keys share 401 and `invalid_api_credentials`; audit/storage failures produce
safe 503. Browser cookies and query credentials never authenticate the API.

REF-73 grants active Community users read access to every Current Dataset. Removing
team/maintenance/export rights therefore does not remove viewing. Owner disable/
delete causes 401 at authentication. Tests also change the authoritative Dataset
view decision after an earlier successful read and prove 404 precedes a possible
304. Inaccessible and missing targets share `not_found` 404, with no input echo.

REF-40 payload-free authentication audits and last-used updates continue, including
304s. Data reads add no event. The audit catalog/inventory/matrix trace the four API
routes and new shared read/bulk-view operations. Normal CLI logs redact the entire
API target; external proxies/ASGI hosts must apply the documented equivalent.
Errors never expose raw keys, headers, identifiers/values, SQL or exception payloads.

## Caching and ETags

Strong SHA-256 ETags bind contract/representation/pagination, owner, total, Current
publication identities and actual Dataset metadata. The metadata contribution avoids
stale responses after ADR-017 name/description/icon edits. A newly selected publication
changes the ETag. Authorization and Record existence precede conditional evaluation;
a matching tag avoids Field/Record-value queries and serialization.

`If-None-Match` is authoritative and supports lists, weak matches and `*` for existing
representations. 304 has no body and retains validators/security/cache headers.
`Last-Modified` is publication time (page maximum, Unix epoch for an empty Dataset page).
Date-only validation returns 200 when membership/deletion, metadata updates, activation
or subsecond precision prevent proof of unchanged representation. Prefer ETags.

Success/304: `private, no-cache, max-age=0, must-revalidate`;
errors: `private, no-store`; `Vary: Authorization, Accept-Encoding` throughout.
No API Set-Cookie/CORS/session fallback. JSON adds nosniff, frame denial, no-referrer
and restrictive CSP. See [ADR-019](../architecture/adr-019-current-rest-reads.md).

## Query and performance evidence

- Dataset pages use SQL Current ranking, count and LIMIT/OFFSET; no Dataset-by-Dataset
  authorization or content load. Bulk rights come from the existing policy service.
- Record pages select at most limit identities using SQL typed ordering/LIMIT/OFFSET,
  then one joined query restricted to those identities. Tests replace the iterator
  with a guard rejecting an unbounded call or more than 100 IDs.
- Exact negative/large Integer/Decimal, UTF-8 String and fractional Timestamp ordering
  is compared with the existing browser service on both engines. Composite lookup
  and all nine canonical datatypes are exercised.
- Record lookup uses the existing version/identifier uniqueness constraint/index.
- Concurrent publication tests (SQLite WAL and PostgreSQL repeatable read) publish
  while a read is paused: first response is wholly version 1, next wholly version 2.
- Database CPU can still scan/sort candidate identifiers and large offsets; SQLite's
  Decimal comparator executes inside its database sort. Python loads schema plus one
  page, not the full Record set. No new schema/index or unlimited-scaling claim.

Measured on **both SQLite and PostgreSQL 16**: Record pages of 1/50/100 rows
all used **10 SELECTs**, including authentication/authorization. Dataset pages of
1/5/100 used **6 SELECTs** each with nine Current Datasets. The guarded Record fixture
has 111 published rows. Evidence: `/private/tmp/ref107-query-evidence.log`;
4 passed, 1 existing warning in 3.43s. Query count does not grow per returned item;
SQL page selection and restricted value loading are explicitly asserted.

## Validation

Environment: Python 3.12.2; SQLite 3.51.0; PostgreSQL 16.15
(`postgres:16`, Debian 16.15-1.pgdg13+2); FastAPI 0.141.1; Starlette 1.6.0 (ASGI
infrastructure only); Pydantic 2.13.5 / pydantic-core 2.46.5; SQLAlchemy 2.0.52;
Flask 3.1.3; a2wsgi 1.10.10; Uvicorn 0.52.4; psycopg 3.3.4; pytest 8.4.2;
Ruff 0.16.5; pre-commit 4.6.2; Node 24.9.0; Playwright 1.61.0;
Chrome 153.0.8010.50.

PostgreSQL uses isolated fixture schemas in the disposable `refrncr-ref165-tests`
container on loopback port 55465. The developer database on port 5432 was untouched.
Commands use repository `.venv/bin/python` (equivalent to activated `python -m ...`).

Before pytest, all four requested gates passed:

```sh
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
git diff --check
```

Untracked files were also checked with pre-commit `--files` without staging.

| Run | Exact result | Evidence |
| --- | --- | --- |
| Targeted API/edges/audit/package CLI, both engines | 183 passed, 1 warning, 61.45s | `/private/tmp/ref107-targeted-final.log` |
| Full SQLite selection | 2654 passed, 1152 PostgreSQL cases deselected, 9 warnings, 368.34s | `/private/tmp/ref107-sqlite.log`, `.xml` |
| Full PostgreSQL-16-enabled suite (also runs SQLite cases) | 3806 passed, zero failures/skips, 14 warnings, 836.39s | `/private/tmp/ref107-postgresql.log`, `.xml` |
| Chrome ASGI browser/API | Six scenarios passed: 320/768/1440 px × JS on/off | `/private/tmp/ref107-browser.log` |
| `pip check` | No broken requirements found | Local interpreter |

The 9 SQLite / 14 combined warnings are the existing Starlette/httpx deprecation,
Python 3.12 SQLite datetime-adapter deprecations and pytest `record_property` versus
JUnit xunit2 warnings. No test assertion was weakened, skipped or marked xfail.
Final Ruff, formatting, pre-commit (tracked and untracked files), diff checks and
staging-list comparison pass; the Git index remains empty.

Full-suite commands (test-only disposable URL):

```sh
export REFRNCR_TEST_POSTGRES_URL='postgresql+psycopg://refrncr:refrncr@127.0.0.1:55465/refrncr_test'
.venv/bin/python -m pytest -k 'not postgresql' -q --junitxml=/private/tmp/ref107-sqlite.xml
.venv/bin/python -m pytest -q --junitxml=/private/tmp/ref107-postgresql.xml
```

Targeted command: `python -m pytest tests/test_rest_api.py tests/test_rest_api_edges.py
 tests/test_audit_catalog.py tests/test_packaged_cli.py -q --maxfail=3` with the same
PostgreSQL test URL. The full suite includes unchanged import/export/version/audit,
security, migrations, Flask/session/CSRF, packaging and CLI regression tests.

The in-app Browser backend was unavailable after following its skill bootstrap.
The existing installed Playwright/Chrome fallback tested a disposable local ASGI app:
`python tests/browser_rest_api_app.py`, then `node tests/javascript/rest_api_browser.cjs`
with `REFRNCR_PLAYWRIGHT_MODULE` and `REFRNCR_BROWSER_EXECUTABLE` pointing at the
installed local tools. It proves Flask login/Records/assets/CSRF, browser-cookie
rejection by API, all four endpoints, 304 and browser key revocation → API 401.
No real credentials/data, secret screenshots or repository browser dependency added.

Earlier investigation runs found two fixture/assertion problems (fixed-clock metadata
and Decimal exponent rendering), and an incorrect static asset path. Those were corrected;
no production behavior was weakened to pass tests. Initial shell Git writes and Docker
inspection required sandbox escalation, then succeeded. No unresolved approval rejection.

## Dependencies and package checks

FastAPI already existed. Restrict its tested minor line to `>=0.141.1,<0.142`;
Pydantic is now explicit (`>=2.13.5,<2.14`) because public models import it directly.
No other production dependency added. Existing wheel/sdist and installed-console tests
pass, including a live installed `/api/v1/datasets` request yielding safe 401 alongside
browser/static assets. `pip check` passes.

`pip download --only-binary=:all: --no-deps` retrieved the exact tested wheels;
ZIP CRC/integrity, distribution name/version and Requires-Python were checked.
Downloaded hashes match official PyPI JSON metadata; all three artifacts are not yanked.
SHA-256:

| Wheel | SHA-256 |
| --- | --- |
| fastapi-0.141.1-py3-none-any.whl | bfb91aa2d334c61cb35ba9a116fc123b3d3df31640b801cf57a7a78ec3f603b3 |
| pydantic-2.13.5-py3-none-any.whl | 346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73 |
| pydantic_core-2.46.5-cp312-cp312-macosx_11_0_arm64.whl | a39ac25a9a2fa4072efdb429833c4a4c8009a51ff9eea3eeae131713cd27991e |

Evidence: `/private/tmp/ref107-dependency-wheels/` and
`/private/tmp/ref107-dependency-wheels.log`. Hashes record tested artifacts and match upstream metadata, not a
new lockfile, signature verification or vulnerability-scan claim. No configured
SAST/secret scanner/SBOM/release pipeline is invented.

## Impact, limits and review status

- Authorization: same REF-73 policy, new bulk form; no per-key scopes or legacy grants.
- Database/migrations: no schema change; additional existing-port queries and SQLite
  connection collation. Existing atomic publication and audit boundaries remain.
- API: additive v1 read contract; generated schema only, documentation delivery deferred.
- Security: safe errors/private revalidation and API path-log redaction; external
  logging systems need equivalent configuration. API keys still incur audit writes,
  owner locking and retention growth on every authentication, including 304.
- Performance: candidate sorting and large offsets remain nonconstant; no guarantee
  of one publication across multiple client page requests. REF-164 remains separate.
- Caching: ETags are the correctness mechanism; date-only requests can be less efficient
  due to independently mutable metadata, activation and second precision.
- Dependencies: bounded tested minor versions; future updates require compatibility tests.
- Browser: no route/template/session/CSRF rewrite, design token or UI functionality change.
- Documentation: local contract, ADR and audit/API-key guidance updated. Confluence's
  already corrected approved target is preserved; no release/deployment claim published.
- CI: not run remotely because staging/commit/push are explicitly prohibited.

## Changed files and explicit proposed staging list

This is the explicit staging list for a later user-reviewed commit; nothing is staged.

```text
README.md
docs/architecture/adr-009-flask-fastapi-composition.md
docs/architecture/adr-019-current-rest-reads.md
docs/development/api-keys.md
docs/development/audit-coverage.json
docs/development/audit-inventory.json
docs/development/audit-policy.md
docs/development/ref-107-report.md
docs/development/rest-api.md
pyproject.toml
src/refrncr/api/__init__.py
src/refrncr/api/contracts.py
src/refrncr/api/http.py
src/refrncr/api/routes.py
src/refrncr/app.py
src/refrncr/audit.py
src/refrncr/dataset_versions.py
src/refrncr/db/session.py
src/refrncr/ports.py
src/refrncr/published_contracts.py
src/refrncr/record_search.py
src/refrncr/services.py
src/refrncr/version_persistence.py
tests/browser_rest_api_app.py
tests/javascript/rest_api_browser.cjs
tests/test_packaged_cli.py
tests/test_rest_api.py
tests/test_rest_api_edges.py
```

Recommended commit:

```text
feat(api): add current published data REST API (REF-107)
```
