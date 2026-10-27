# User API keys (REF-40)

Active users manage their own credentials under **Settings → API keys**, including
administrators: administration does not grant access to another user's keys.
Names are trimmed, required, limited to 100 input characters and case-insensitively
unique among active keys. At most five unexpired, unrevoked keys may exist per
user. Expired and revoked metadata remain visible. Keys expire after 90 days by
default; a user can select an earlier future date (UTC midnight). Unlimited or
longer validity is rejected. Revocation requires confirmation and the current
revision; it is permanent. Rotate by creating a key, updating the integration,
then revoking the old key. An expired key cannot be reactivated.

## Configuration and verifier

Set `REFRNCR_API_KEY_PEPPER` to 64 hexadecimal characters encoding 32
cryptographically random bytes, injected through protected deployment configuration.
Do not commit it, print it in diagnostics or store it alongside database backups.
Absent configuration disables API-key management; malformed configuration fails
startup. There is no generated, weak or unpeppered fallback.

For local development from a source checkout, create the ignored `.env` file once:

```shell
umask 077
printf 'REFRNCR_API_KEY_PEPPER=%s\n' "$(openssl rand -hex 32)" > .env
.venv/bin/refrncr start
```

The CLI loads `.env` from its current working directory. Values supplied by the
launching environment take precedence. Use the variable name exactly as shown.
The command substitution keeps the generated value out of the literal command,
but the value remains a secret and must not be printed or pasted into logs. Reuse
the same `.env` file across restarts.

An environment variable belongs to the process that inherited it. A server that
is already running cannot observe a later `export`, and a server started in a
different terminal, IDE, container or service does not inherit the current shell's
environment. Stop the existing server and restart it from the configured environment.
For containers and managed services, inject the variable through that platform's
secret/configuration mechanism instead of relying on an interactive shell export.

If **Settings → API keys** still reports that server configuration is required:

1. In the shell or service environment that will launch refrncr, run
   `printf '%s' "$REFRNCR_API_KEY_PEPPER" | grep -Eq '^[0-9A-Fa-f]{64}$' && echo "pepper is valid"`.
2. If it prints nothing, configure a 64-character hexadecimal value there. A
   value such as `9999` is invalid.
3. Fully stop the old refrncr process and start a new process from that same
   environment.
4. For an IDE, container or service manager, configure and restart the process
   through that tool; exporting in an unrelated terminal has no effect.

Do not troubleshoot by printing the pepper or by inspecting a complete process
environment in shared logs. Confirm only its presence, length and hexadecimal
format. Use the ignored `.env` file locally or arrange protected, persistent
injection through the deployment platform.

A credential is `rfr_` + 32 lowercase UUID hex characters + `.` + 43 URL-safe
characters generated from 32 random bytes (256 bits of entropy): 80 characters
in total. The `Bearer ` header value is 87 characters. Only the UUID/prefix is
public. The verifier is hexadecimal HMAC-SHA256 over the domain separator
`refrncr.api-key.v1`, one NUL byte, and the ASCII credential, using the pepper as
HMAC key. Comparisons use `hmac.compare_digest`; malformed stored verifiers fail
closed. Constant-time comparison does not imply identical whole-request timings.

Back up and restore the pepper separately and securely. Replacing it invalidates
all existing keys; there is no old-pepper grace period. Plan a maintenance window,
revoke old keys while the old configuration is available, replace the pepper,
and issue replacement credentials after restart. Ordinary per-key rotation does
not require pepper replacement. Losing the pepper requires replacement keys.

## One-time display and browser security

Only the initial successful creation POST returns the raw secret. GET, list,
errors and replayed creation requests return metadata only. A signed,
session-bound form identity prevents duplicate creation from replay. Existing
session authentication and CSRF protection apply; keys never log users into the
browser. Responses use `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
The secret is absent from URLs, cookies and browser storage. The JS controller
clears its field on pagehide; API-key pages disable cross-document view transitions.
The tested Chrome history traversal refuses to restore the non-cacheable POST.
Clipboard copying is an explicit user action; the clipboard remains user-controlled.

The warning and secret field are labelled. Copy reports success or selects the
field with instructions when clipboard access fails. Without JavaScript users
can manually select the secret and complete creation/revocation through normal
page navigation. Shared error-summary and status focus behavior is retained.
The metadata table scrolls inside its labelled keyboard-accessible region at
small widths, without page overflow. All names are escaped.

## Authentication boundary for REF-107

`ApiKeyService.authenticate(bearer_value)` accepts only the Authorization header
value, for example the `Bearer ` prefix followed by a credential. It does not
read cookies, sessions, query parameters or request objects. The future HTTP
adapter must supply exactly one Authorization value. No Dataset/Record endpoint,
FastAPI route or OpenAPI document is added by REF-40.

A successful call returns `AuthenticatedPrincipal(owner_id)` after verifying
current active/nondeleted owner, verifier, expiry and revocation. Existing Dataset
authorization must resolve current permissions for that ID on every operation;
the key caches no rights. Credential failures raise `ApiKeyAuthenticationError`
with status 401 and body `{"error": "invalid_api_credentials"}` irrespective of
reason. Database/audit failures fail closed and are operational failures; a future
adapter must use a safe availability response, never authenticate through them.
Do not log Authorization headers, request bodies, secrets or verifiers in the
application, proxy or monitoring stack. Disable SQL bind/result debug logging in
production because verifier data is persisted. REF-31 owns broader security logging.

## Persistence, concurrency and audit

`api_keys` stores UUID, owner FK, name, verifier, created/expiry/revoked/last-used
timestamps and revision. Public service views contain no verifier. Owner-row locks
serialize creation/revocation on PostgreSQL; SQLite uses the existing serialized-write
UoW. Authentication never locks the owner row. It locks the key for revocation ordering
and uses one atomic conditional update: `last_used_at` changes only when absent or older
than the 15-minute cutoff. It is an approximate recent-use indication, not a per-request
timestamp. Concurrent requests require no retry; at most one refresh wins. Successful
304 responses qualify, while every rejected credential leaves the value unchanged.

New requests durably audit `api_key.created`, `api_key.revoked` and
`api_key.authentication_failed`, all without detail payload. Failed authentication uses
a fresh attempt UUID and null actor, displayed as `Unauthenticated`. This is the
product-owner-approved exception to REF-149, constrained to that exact action and
object type in both code and schema. No guessed actor or claimed key is recorded.
Historical `api_key.authenticated` rows remain valid and present with their original owner;
successful authentication no longer creates that per-request durable event. Audit failure
rolls back creation or revocation. Last-used persistence failure fails authentication closed.
Failure-event
persistence commits before the generic credential exception is returned.

## Migration and operation

Alembic `0021_ref_40` (file `0021_ref_40_api_keys.py`) follows `0020_ref_162`.
It adds the key table and narrowly permits the null audit actor. Stop writers,
take a database backup and retain the matching pepper securely before upgrading
through the established migration workflow. Existing users and audit history
are preserved. An empty installation can downgrade; downgrade refuses any key
rows or null-actor audit history rather than dropping security records. For a
populated rollback restore the pre-upgrade backup and matching application/config.

No dependency, Cloud tenant model, external secrets service, API scope system or
retention purge is introduced. Key/audit history grows under existing retention
policy. Rate limiting and the future HTTP adapter remain outside REF-40.
See [ADR-018](../architecture/adr-018-user-api-keys.md) and the
the archived REF-40 validation report.

## REF-107 HTTP integration

The [Current REST API](rest-api.md) now supplies exactly one Authorization value
and calls the existing authentication service. It mounts before Flask at `/api/v1`;
keys never authenticate browser routes. Data reads and conditional 304s recheck
current Dataset authority. Missing/invalid keys use the existing 401 contract;
operational authentication/audit failures return a fixed 503. CLI access logs redact
the entire API target so business identifiers and query attempts are not recorded.
This local implementation supersedes the future-adapter wording above; public
OpenAPI/Swagger is public under REF-109; see [documentation security](rest-api.md#public-openapi-and-swagger-ref-109).

REF-306 makes data reads follow the owner's current dataset export right under the
installation policy. In either stricter mode, Settings → API keys shows “Keys use your
current permissions and read only the datasets you can export.” below Create API key,
for administrators and ordinary users. Everyone keeps the shorter sentence “Keys use
your current permissions.” Key creation and ownership rules remain unchanged;
the explanation grants no authority to read or change administrator-only settings.
# Security audit boundary

Creation, revocation and successful authentication retain their existing transactional `api_key.*`
events. Invalid, expired, revoked and otherwise unusable credentials retain the uniform
`api_key.authentication_failed` event and public `invalid_api_credentials` response. Neither stored
nor rendered audit data contains the bearer value, verifier, key name, claimed public key ID or
request headers.
