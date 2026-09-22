# REF-31 implementation report

## Decisions and boundaries

REF-31 extends the existing append-only `AuditEvent` history and administrator-only REF-39
presentation. It introduces no second event store, SIEM integration, retention process, IP storage,
new role or Cloud concern. REF-149 remains the catalog/inventory/coverage authority. REF-70 remains
stdout operational JSON logging and never copies durable security-audit payloads.

The final worktree is separated from the unfinished CLI/REF-70 debug-logging follow-up. It contains
no interactive console formatter, request aggregation, `--log-level` CLI option, startup-output
change or Flask route-template handoff. The only CLI/package changes recognize and package the
REF-31 migration.

Successful permission/access changes, API-key lifecycle/authentication, confirmed imports, prepared
exports and Dataset publication already use catalogued events in the same transaction as their
successful domain action (with the documented prepared-export transaction boundary). REF-31 adds
the missing content-free failure contracts:

| Event | Actor |
| --- | --- |
| `security.login_failed` | Unauthenticated |
| `security.session_rejected` | Unauthenticated |
| `security.authorization_denied` | Resolved session actor when known, otherwise unauthenticated |
| `security.csrf_rejected` | Resolved session actor |
| `security.workflow_token_rejected` | Resolved session actor |

Existing `api_key.authentication_failed` continues to cover invalid, expired and revoked API keys
without disclosing which condition applied.

## Privacy and redaction

Security events have a generated attempt UUID and `details = null`. They never persist or render
passwords, account identifiers, API keys/verifiers, bearer/session/CSRF/review/import tokens,
connection strings, request bodies, uploads, search text, filenames, raw paths or client IPs.
Unknown/unavailable targets use the existing safe object presentation. User-facing authentication,
authorization and token responses remain unchanged and non-disclosing. Ordinary form validation
does not create a security event.

## Changed files

- Audit contracts/presentation/model: `src/refrncr/audit.py`, `audit_presentation.py`, `models.py`.
- Browser boundaries: `src/refrncr/web.py`.
- Migration orchestration: `src/refrncr/cli.py` recognizes revision `0022_ref_31` in the existing
  preflight and upgrade paths.
- Constraint migration: `0022_ref_31_security_audit.py`.
- Coverage/inventory and security/operations/API-key/session documentation.
- Catalog, history, migration and security regression tests.

## Validation

| Command/profile | Result |
| --- | --- |
| Focused audit/security/API-key/history tests | 150 passed; 71 PostgreSQL-profile skips before PostgreSQL validation. |
| Focused REF-31/catalog/migration tests with PostgreSQL 16 | 80 passed. |
| Full SQLite profile, including packaged live-loopback tests | 2,709 passed; 1,171 PostgreSQL-profile skips; 3 existing warnings; 370.34 s. |
| Full PostgreSQL 16.15 profile against the disposable loopback test container | 3,880 passed; 0 skipped; 3 existing warnings; 963.68 s. |
| `.venv/bin/python -m ruff check .` | Passed. |
| `.venv/bin/python -m ruff format --check .` | Passed; 230 files already formatted. |
| `.venv/bin/python -m pre_commit run --all-files` | Passed all configured hooks. |
| `git diff --check` | Passed. |

GitHub Actions remains the independent pre-merge gate.

## Explicit staging list

```text
docs/development/api-keys.md
docs/development/audit-coverage.json
docs/development/audit-inventory.json
docs/development/audit-policy.md
docs/development/browser-session-security.md
docs/development/operations.md
docs/development/ref-31-report.md
src/refrncr/audit.py
src/refrncr/audit_presentation.py
src/refrncr/cli.py
src/refrncr/migrations/versions/0022_ref_31_security_audit.py
src/refrncr/models.py
src/refrncr/web.py
tests/dataset_helpers.py
tests/test_audit_catalog.py
tests/test_audit_coverage.py
tests/test_audit_history.py
tests/test_audit_history_review.py
tests/test_dataset_deletion.py
tests/test_dataset_diff_views.py
tests/test_dataset_version_migration.py
tests/test_field_datatype_services.py
tests/test_migrations.py
tests/test_packaged_cli.py
tests/test_recovery.py
tests/test_security_audit.py
```

Recommended commit message: `feat(security): add durable security audit events (REF-31)`.

## Operational limitations

Audit retention equals database retention and remains operator governance. Network attribution uses
independent reverse-proxy/hosting logs. There is no alerting, anomaly detection, export or SIEM
integration. Local validation does not replace GitHub Actions before merge.
