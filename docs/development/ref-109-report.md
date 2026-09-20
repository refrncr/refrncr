# REF-109 — Public OpenAPI documentation

Status: technically ready for local review on `feat/ref-109-openapi-documentation`.
Base: current main `0f2aab86768d7183ed91597b9e5065ca8bcd0656`.
Nothing staged, committed or pushed. Independent GitHub Actions CI remains the
pre-merge gate; it cannot run for this deliberately unpushed change.

## Scope and sources

Read the applicable repository AGENTS.md, complete REF-109/107/40, ADR-009/018/019,
REST API, API-key, audit-policy, browser-session-security and UI-foundation guides,
existing composition, contracts, security headers and relevant tests. This is
Community/core work. The user explicitly corrected Jira's obsolete Starlette-browser
wording: FastAPI remains the ASGI entrypoint with the existing a2wsgi Flask mount.
Confluence API Architecture & Effective Dating, version 11, already records that
superseding target and the public documentation paths. The implementation does not
depart from that target, so no Confluence modification was necessary.

| Public GET path | Content and authentication |
| --- | --- |
| `/api/openapi.json` | Generated OpenAPI 3.1 JSON; anonymous, no business data |
| `/api/docs` | Local Swagger UI; anonymous, no business data in delivered HTML |
| `/api/v1/datasets` | Existing Bearer-protected Current Dataset collection |
| `/api/v1/datasets/{dataset_key}` | Existing Bearer-protected Current Dataset/Fields |
| `/api/v1/datasets/{dataset_key}/records` | Existing Bearer-protected Current Records |
| `/api/v1/datasets/{dataset_key}/records/{identifier}` | Existing Bearer-protected lookup |

Only these six GET operations appear in OpenAPI. Browser, health, key-management,
internal and unimplemented operations are excluded. There is no new data endpoint,
API version, role, key rule, domain operation, table or migration. Concepts,
Scheduled/Expired/Cancelled, history, effective-date/version selection, filtering,
search/sort, writes/import/export, mappings and hierarchies remain excluded.

The root combines actual generated schemas from the mounted API and its two
registered documentation routes, adding the existing public mount prefix. Existing
Pydantic response models remain authoritative. No separately maintained specification
is introduced. Parameter/header/operation descriptions and safe error examples live
with the route/model metadata. FastAPI's implicit 422 is removed because the actual
boundary returns 400. Collection limits/defaults, canonical values, opaque single
and composite identifiers, safe pagination links, 401/404/405/503 and ETag/Last-Modified/
If-None-Match/304/private cache semantics are described.

Necessary supporting fixes: the unchanged exact-Decimal JSON renderer now inherits
JSONResponse so FastAPI generates success-model references; previously the generated
200 schema was empty. The audit inventory guard resolves nested module paths to
check the two new explicitly excluded documentation operations. Neither changes the
REF-40 authentication rules or REF-107 data serialization/service behavior.

## Authentication, secrets and security choices

The documentation routes call no data or authentication services and emit no audit.
Try it out calls the unchanged protected data endpoints and produces the existing
payload-free REF-40 authentication events. Browser sessions do not authenticate API
requests; Swagger's fetch interceptor explicitly omits cookies.

HTTP Bearer remains the security scheme. Swagger retains authorization only in its
current page memory: no local/session storage, URL configuration, credential examples
or deep-link history. The input is masked and not serialized into HTML. Pagehide
clears authorization and rendered content; back/forward cache restoration reloads
cleanly. Curl/request-snippet components are removed entirely because upstream would
include the credential in generated command text. The UI restricts outgoing requests
to local read-only API/documentation targets; no external validator or CDN is enabled.
This does not protect against a compromised browser or a user manually sharing a key.

Swagger UI 5.33.0's upstream JS/CSS are shipped byte-for-byte in the existing static
package paths, with Apache-2.0 and bundled dependency notices. npm archive SHA-512
integrity and asset SHA-256 hashes are recorded in ASSET-SOURCES.md. There is no new
Python production dependency, Node runtime/build step, or external runtime service.
The 1.59 MB bundle is a deliberate package-size cost. Narrow pre-commit exemptions
preserve upstream bytes and allow this one large distribution; first-party checks
remain enabled. Future Swagger updates require explicit hash/license/browser review.

Documentation uses no-store, no-referrer, nosniff, frame denial and a strict page CSP:
self-hosted script/style/font/connect, no unsafe-inline or unsafe-eval, no base or form
navigation. `img-src data:` supports Swagger's embedded images. API JSON retains its
restrictive existing CSP; Flask headers/session/CSRF/static behavior are unchanged.
The CLI's existing full-API-target redaction and payload-free audit remain intact.
Independent ASGI hosts/proxies still require the documented equivalent log policy.

## Accessibility and responsive evidence

UI classification: product functionality with deliberate page-specific Swagger CSS
and initializer. No design tokens, global components or component variants changed.
Existing color, spacing, font and focus tokens are reused. A consistency review
checked repeated sizing, selector scope, landmarks, focus and authorization states.

Real Chrome checks cover 320/768/1440 px: one h1/main, page title, no page overflow,
visible keyboard focus, Enter activation, dialog Tab containment, Escape and opener
restoration, masked input, Authorize/Try it out, response/error rendering, cleared
history state and no-JavaScript fallback. The raw OpenAPI JSON link remains available
without scripts. Screenshot review also caught and corrected overly narrow desktop
table columns; overflow checks alone were insufficient.

Upstream GET/Cancel contrast and copy-control target size were corrected locally;
labels retain their visible button text. The controller supplies missing dialog/close
labels and focus management, including rerendered authorizers and replacement inputs.
Nine axe-core 4.11.1 checks (three widths × resting/operation/authorization) use WCAG
2.0/2.1/2.2 A/AA tags. See final results below. Axe reports manual-review items for
upstream ARIA attributes and some contrast contexts; automated checks do not establish
complete WCAG conformance. No screen-reader-user session, OS clipboard test or
cross-browser certification was performed. Raw JSON remains the accessible alternative.

Evidence is local and contains only disposable fixture data, never real credentials:
`/private/tmp/ref109-browser-evidence/authorize-{320,768,1440}.png` and
`try-it-out-{320,768,1440}.png`; browser and axe logs are listed below.

## Validation and versions

Python 3.12.2; SQLite 3.51.0; PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2), in the
existing isolated `refrncr-ref165-tests` container on loopback port 55465. The developer
database on port 5432 was untouched. Tests use independent fixture schemas.
FastAPI 0.141.1; Starlette 1.6.0; Pydantic 2.13.5; SQLAlchemy 2.0.52; Flask 3.1.3;
a2wsgi 1.10.10; Uvicorn 0.52.4; psycopg 3.3.4; pytest 8.4.2; Ruff 0.16.5;
pre-commit 4.6.2; Node 24.9.0; Playwright 1.61.0; Chrome 153.0.8010.50;
Swagger UI 5.33.0; temporary openapi-spec-validator 0.7.2 and axe-core 4.11.1.

Before pytest, the requested gates ran successfully using repository `.venv`:

```sh
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pre_commit run --all-files
git diff --check
```

New untracked files also receive explicit pre-commit `--files` checks without staging.
All final commands/results are recorded below; earlier failures are not reported as
passes. Final affected checks are rerun after documentation/UI refinements.

| Check | Result | Evidence |
| --- | --- | --- |
| Full SQLite selection | 2658 passed, 1155 deselected, 9 warnings, 448.93s | `/private/tmp/ref109-sqlite.log`, `.xml` |
| Full PostgreSQL-16-enabled suite (includes SQLite) | 3814 passed, 14 warnings, 886.00s | `/private/tmp/ref109-postgresql.log`, `.xml` |
| Final documentation/audit/package tests, both engines | 68 passed, 1 warning, 26.53s | `/private/tmp/ref109-final-targeted.log` |
| Swagger browser acceptance | All three widths passed; screenshots visually reviewed | `/private/tmp/ref109-browser-final.log` |
| Existing Flask browser regression | All six width/JavaScript combinations passed | `/private/tmp/ref109-flask-browser.log` |
| Axe WCAG A/AA checks | Nine checks with zero violations; manual-review items retained | `/private/tmp/ref109-axe-final.log` |
| Independent OpenAPI 3.1 validator | Passed | Temporary validator; no project dependency |
| `python -m pip check` | No broken requirements found | Repository interpreter |

Full-suite commands (disposable test URL):

```sh
export REFRNCR_TEST_POSTGRES_URL='postgresql+psycopg://refrncr:refrncr@127.0.0.1:55465/refrncr_test'
.venv/bin/python -m pytest -k 'not postgresql' -q --junitxml=/tmp/ref109-sqlite.xml
.venv/bin/python -m pytest -q --junitxml=/tmp/ref109-postgresql.xml
```

Focused: `python -m pytest tests/test_api_documentation.py tests/test_audit_catalog.py
 tests/test_packaged_cli.py -q` with the same URL. These verify complete generated
response-model equality, resolvable schema references, security/parameters/statuses,
headers, anonymous data-free/no-audit docs, safe errors, local assets and installed
wheel/sdist/CLI HTTP startup. Existing full suites cover invalid/revoked keys,
browser-cookie rejection, exact canonical values, audit privacy/log redaction,
Flask sessions/CSRF/assets, migrations and all other repository functionality.

Browser: start `python tests/browser_rest_api_app.py`; run
`node tests/javascript/api_docs_browser.cjs` with `REFRNCR_PLAYWRIGHT_MODULE`,
`REFRNCR_BROWSER_EXECUTABLE` and optional `REFRNCR_BROWSER_OUTPUT` pointing to local
test tools/output. The in-app Browser backend was unavailable after skill bootstrap;
the existing project Playwright/Chrome convention was used. The prior temporary
Playwright files were incomplete, so version 1.61.0 was restored under `/tmp` only.
No real account, key or Dataset was used. The browser script checks outgoing origin,
headers/cookies, HTML/URL/storage privacy and actual authorization state after history.

Earlier investigation corrected empty generated success schemas, missing dependency
parameter descriptions, nested audit module resolution, Swagger landmark/focus/contrast
issues, and test selectors/waits for upstream accessible names and async rendering.
No test was weakened to accept an incorrect data contract. Warnings are the existing
Starlette/httpx, SQLite datetime adapter and pytest/JUnit record_property deprecations.

## Remaining risks and impact review

- No database/migration, domain authorization, key lifecycle, tenant-isolation, API
  response-contract or new-version change. Community remains installation-scoped.
- Public documentation intentionally reveals endpoint structure, never installation
  data. Entered keys and fetched authorized data exist transiently in browser memory.
- Swagger is third-party code vendored into the package; update maintenance is manual.
  No SAST, secret scanner, SBOM or vulnerability-scan result is claimed.
- Accessibility evidence covers Chrome and structural/automated/manual visual checks;
  upstream manual-review items and assistive-technology limits remain explicit.
- External hosting/proxy logs must preserve the established API credential/target
  redaction policy. No new general security-logging infrastructure is introduced.
- GitHub Actions must pass before a later merge; no remote CI or release claim.

## Changed files and explicit proposed staging list

The following is a proposed list for the user's later reviewed commit. Nothing is staged.

```text
.pre-commit-config.yaml
README.md
docs/architecture/adr-009-flask-fastapi-composition.md
docs/architecture/adr-019-current-rest-reads.md
docs/development/api-keys.md
docs/development/audit-coverage.json
docs/development/audit-inventory.json
docs/development/audit-policy.md
docs/development/ref-109-report.md
docs/development/rest-api.md
docs/development/ui-foundation.md
src/refrncr/api/contracts.py
src/refrncr/api/documentation.py
src/refrncr/api/http.py
src/refrncr/api/routes.py
src/refrncr/app.py
src/refrncr/audit.py
src/refrncr/audit_guard.py
src/refrncr/static/css/layouts/api-docs.css
src/refrncr/static/css/swagger-ui.css
src/refrncr/static/js/api-docs.js
src/refrncr/static/js/swagger-ui-bundle.js
src/refrncr/static/licenses/ASSET-SOURCES.md
src/refrncr/static/licenses/LICENSE-Swagger-UI-bundle.txt
src/refrncr/static/licenses/LICENSE-Swagger-UI.txt
src/refrncr/static/licenses/NOTICE-Swagger-UI.txt
src/refrncr/templates/pages/api/docs.html
tests/javascript/api_docs_browser.cjs
tests/test_api_documentation.py
tests/test_audit_catalog.py
tests/test_packaged_cli.py
tests/test_rest_api.py
```

Recommended commit:

```text
feat(api): publish OpenAPI documentation (REF-109)
```

## UI review follow-up

Removed the skip link at the product owner's request, together with its dedicated
styling. The raw OpenAPI link and no-JavaScript fallback remain available.
Documentation tests passed on SQLite and PostgreSQL (7 passed); Ruff, formatting,
pre-commit and diff checks were rerun for this page-specific adjustment.
