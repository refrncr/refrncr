# REF-40 implementation and local review report

Branch: `feat/ref-40-api-keys`, based on updated `origin/main` (`9507a68`).
Scope: Community/core user credentials; future read-only endpoints and API
reference remain REF-107/REF-109. Nothing staged, committed or pushed.

## Decisions and behavior

Implemented Settings management for each active user's own keys, including
metadata/history, trimmed bounded case-insensitively unique active names, a
transactional maximum of five active keys and default 90-day lifetime with shorter
future validity only. Expiry and permanent confirmed revocation deny subsequent
authentication. Current owner status and Dataset authorization remain authoritative;
no permission scope snapshots, browser key login or Cloud tenant model were added.

Credentials contain 256 random bits and a public UUID. Persistence stores metadata
and a domain-separated HMAC-SHA256 verifier, never the raw credential. A protected
32-byte configuration pepper is mandatory for enabled key management. Missing
configuration disables it; malformed configuration fails startup. Constant-time
verifier comparison, bounded Bearer parsing, uniform credential failures and safe
DTOs prevent raw secret/verifier disclosure through normal interfaces. See
[configuration, rotation and integration](api-keys.md) and
[ADR-018](../architecture/adr-018-user-api-keys.md).

The first successful creation POST displays the secret once. Session-bound signed
form identity prevents replay; GET/list/errors cannot recover the secret. Responses
are no-store/no-referrer. No URL, session or browser storage contains the secret.
The copy controller reports success/fallback and clears the field on pagehide.
Browser history cannot restore the tested non-cacheable POST. Rotation uses a new
key, integration update and old-key revoke; pepper replacement invalidates all
existing keys and requires coordinated replacement.

The reusable authentication service accepts a Bearer header value and returns the
owner principal after current eligibility checks. Existing authorization resolves
current rights per operation. It exposes a fixed 401/error contract for credential
rejection and fails closed on operational/audit failures. No new data endpoint was
introduced. Owner locks and key refresh serialize creation, revoke and authentication;
SQLite uses the existing serialized-write UoW. Revisions reject stale/replayed revoke.

## Audit and migrations

The product owner explicitly approved nullable actor **only** for failed API-key
authentication. Contract and database check enforce the restricted exception to
REF-149. Failure records contain a fresh attempt UUID and show `Unauthenticated`
without an actor link. Created/revoked/success events require a real user and key
UUID. All four events have no detail payload or raw request information. Catalog,
coverage, inventory, presentation and bypass guard were updated. Audit failure
rolls back lifecycle/last-used changes; credential rejection follows failure-audit
commit.

Migration `0021_ref_40` adds `api_keys` and the constrained nullable audit actor.
Real upgrade/empty-downgrade tests preserve existing users and audit data on both
databases. Populated downgrade is explicitly rejected for key rows or anonymous
audit history. Operators stop writers and back up before migration; populated
rollback requires the matching pre-upgrade backup/application/configuration.
Existing historical-migration fixtures and CLI revision allowlists were updated
for the new head, preserving their original schema/content assertions.

A [Confluence architecture draft](https://cleverhorizons.atlassian.net/wiki/pages/resumedraft.action?draftId=53116929&draftShareId=17c4fac5-aa84-4664-bf95-9a2a434d10de)
records the approved decision and operational consequences. It remains a draft
because this implementation is awaiting local review and independent CI.

## UI and accessibility

Classification: product functionality using shared Settings, fields, buttons,
feedback, table and focus patterns. No changed design tokens or component variants.
Global navigation now exposes Settings to regular users and API keys in Settings;
Users remains administrator-only. Page-specific rules disable view transitions
for sensitive API-key pages and wrap long headings. Forms use full pages, so no
new modal or panel focus trap is needed.

Chrome checks cover JavaScript enabled/disabled at 320, 768 and 1440 px, labelled
fields and secret, validation feedback, JS error/warning/revoke/status focus,
copy feedback, keyboard Enter navigation, permanent revoke, history and absence
of horizontal page overflow. The small-screen metadata table has its own labelled,
keyboard-accessible scroll region. No-JavaScript uses manual secret selection and
normal form/link navigation. Existing shared visible-focus/contrast tokens remain.
Screenshots contain metadata only, never the one-time secret; visually inspected
320, 768 and 1440 px samples. No claim of an independent WCAG audit is made.

The in-app Browser runtime returned `Browser not available: iab`; validation used
the installed external Playwright/Chrome fallback against a disposable loopback
fixture. No real account/data was used. Browser script:
`tests/javascript/api_keys_browser.cjs`; fixture: `tests/browser_api_keys_app.py`.
Temporary evidence: `/private/tmp/ref40-browser-final.log` and
`/private/tmp/ref40-{320,768,1440}-{true,false}.png`.

## Validation

Versions: Python 3.12.2, SQLite 3.51.0, PostgreSQL 16.15 (disposable `postgres:16`
container), SQLAlchemy 2.0.52, Node 24.9.0, Playwright 1.61.0 and Google Chrome
153.0.8010.50. PostgreSQL tests use isolated schemas on loopback port 55465;
the developer database/container on port 5432 was not modified.

Before pytest: `.venv/bin/python -m ruff check .`,
`.venv/bin/python -m ruff format --check .`,
`.venv/bin/python -m pre_commit run --all-files`, and `git diff --check` passed.
Untracked files were separately passed to pre-commit with `--files`; no staging
was needed. Existing hooks with no applicable files report skipped hooks, not
skipped tests. No new test skips, sleeps or weakened assertions were introduced.

- Focused REF-40, migrations, Settings/UI, wheel and CLI: **379 passed**, one
  existing SQLite datetime-adapter deprecation warning, 88.79 seconds.
- Browser acceptance: **6/6 passed**, including JS validation focus and history
  redaction; controller unit test also passed.
- Full SQLite selection: **2587 passed**, 1096 PostgreSQL cases deselected,
  zero failed/skipped, nine warnings, 541.57 seconds. Evidence:
  `/private/tmp/ref40-sqlite-final.log` and `ref40-sqlite-final.xml`.
- Full PostgreSQL-enabled suite: **3683 passed**, zero failed/skipped,
  14 warnings, 961.71 seconds. Evidence:
  `/private/tmp/ref40-postgresql-final.log` and `ref40-postgresql-final.xml`.
  This includes all 1096 PostgreSQL cases, eight REF-40 migration cases across
  both engines and five package/CLI tests.

The full-suite warnings are existing SQLite datetime-adapter deprecations and
pytest `record_property`/JUnit xunit2 compatibility warnings; no new runtime
warning was introduced.

Both full runs set `REFRNCR_TEST_POSTGRES_URL` to the disposable integration
instance. SQLite run selects `-k 'not postgresql'`; the PostgreSQL-enabled run
executes the entire suite, including SQLite and every PostgreSQL parametrization.
Commands use `.venv/bin/python -m pytest -q` with JUnit files under `/private/tmp`.
The focused run includes `test_api_keys.py`, `test_api_key_migration.py`,
`test_packaged_cli.py`, `test_interfaces.py`, and `test_ui_polish.py`.
Full regression includes authorization/security/audit/settings/migration and
existing JavaScript controller tests. Wheel tests verify inclusion of the service,
migration, templates, JS and CSS, alongside CLI-startup behavior.

Initial regression discovered obsolete Settings link counts/regular-user visibility
expectations and a migration fixture stamping current tables as an old revision.
Those tests now assert the new navigation and truly downgrade before upgrading;
existing access-denial/content-preservation assertions remain. Initial SQLite
result was 2571 passed, 5 failed; an obsolete PostgreSQL run was interrupted after
that diagnosis. These are superseded by the final results, not counted as passes.

## Impact review and remaining limitations

- Authorization: owner-only key management, current user eligibility/rights, no
  administrator override; authentication does not bypass Dataset checks.
- Tenant isolation: Community installation/user boundary only; no Cloud tenancy.
- Schema/migration: new key table and the approved narrow actor exception, with
  preservation tests and populated-downgrade refusal.
- API contract: framework-independent Bearer service and uniform credential-error
  contract; HTTP data adapters/OpenAPI remain out of scope.
- Security: protect pepper and backups; configure proxies/observability not to
  capture credentials or SQL bind/results. Clipboard persistence is user-controlled.
- Dependencies: no application dependency or build framework was added. External
  browser tooling is local validation only.
- Documentation/architecture: operating guide, linked security/audit/authorization/
  UI guidance, ADR and Confluence draft updated.
- Operational risks: audit writes and per-owner locks add authentication I/O and
  contention. History grows under existing retention policy. Rate limiting and
  future HTTP error mapping remain integration work. Authentication completed
  before revoke may finish its request; subsequent authentication is denied.
- Independent GitHub Actions CI cannot run for unpushed work and remains required
  before merge. No SAST, secret scanner, formal accessibility certification or
  other planned-but-unconfigured control is claimed.

## Changed files and explicit future staging list

The list below is review guidance only; none of these files has been staged.

```text
README.md
docs/architecture/adr-018-user-api-keys.md
docs/development/api-keys.md
docs/development/audit-coverage.json
docs/development/audit-history.md
docs/development/audit-inventory.json
docs/development/audit-policy.md
docs/development/browser-session-security.md
docs/development/dataset-governance.md
docs/development/ref-40-report.md
docs/development/ui-foundation.md
src/refrncr/api_key_persistence.py
src/refrncr/api_keys.py
src/refrncr/app.py
src/refrncr/audit.py
src/refrncr/audit_guard.py
src/refrncr/audit_presentation.py
src/refrncr/cli.py
src/refrncr/migrations/versions/0021_ref_40_api_keys.py
src/refrncr/models.py
src/refrncr/persistence.py
src/refrncr/ports.py
src/refrncr/static/css/layouts/api-keys.css
src/refrncr/static/js/api-key-secret.js
src/refrncr/templates/base.html
src/refrncr/templates/layouts/settings.html
src/refrncr/templates/pages/api_keys/base.html
src/refrncr/templates/pages/api_keys/create.html
src/refrncr/templates/pages/api_keys/list.html
src/refrncr/templates/pages/api_keys/revoke.html
src/refrncr/templates/pages/api_keys/secret.html
src/refrncr/web.py
tests/browser_api_keys_app.py
tests/dataset_helpers.py
tests/javascript/api_key_secret.cjs
tests/javascript/api_keys_browser.cjs
tests/test_api_key_migration.py
tests/test_api_keys.py
tests/test_audit_catalog.py
tests/test_audit_history.py
tests/test_concept_migration.py
tests/test_dataset_deletion.py
tests/test_dataset_version_migration.py
tests/test_interfaces.py
tests/test_migrations.py
tests/test_new_version_import.py
tests/test_packaged_cli.py
tests/test_ui_polish.py
```

Recommended commit: `feat(api): add secure user API keys (REF-40)`.

`git diff origin/main...HEAD` and its stat are empty intentionally: HEAD still
points to the original main commit. Review the working-tree diff plus the new
files above. `git diff --cached` is empty. Nothing was staged, committed or pushed.
