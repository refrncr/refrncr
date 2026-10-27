# Browser session security

Community browser sessions use a 12-hour absolute lifetime. The server rejects a session at its
stored expiry time, and the browser cookie has a matching 43,200-second `Max-Age`. There is no idle
timeout and session reads do not extend the lifetime. A successful login always creates new random
session and CSRF tokens. Only the SHA-256 session-token digest is stored. Multiple browsers may stay
signed in; signing in again replaces only the valid session presented by that browser.

Cookie behavior depends on the directly observed request and explicit server composition:

* Direct HTTP loopback quick starts, such as `refrncr start` on `127.0.0.1` without trusted-proxy
  configuration, use the host-only
  `refrncr_session` and `refrncr_csrf` cookies. They are `HttpOnly`, `SameSite=Lax`, and `Path=/`;
  the session cookie is deliberately not `Secure` because browsers cannot send it over HTTP.
* HTTPS uses `__Host-refrncr_session` and `__Host-refrncr_csrf`, with `Secure`, `HttpOnly`,
  `SameSite=Lax`, `Path=/`, and no `Domain` attribute.
* Plain HTTP on a non-loopback deployment cannot establish an authenticated browser session. Use
  HTTPS for any externally exposed deployment.
* Configuring any trusted proxy explicitly selects proxy/external deployment mode and disables the
  non-Secure loopback-cookie mode, even when the application bind, upstream peer, and rewritten Host
  are all loopback. Proxy deployments therefore require HTTPS.

Loopback HTTP is enabled only when the server was explicitly composed for a literal loopback bind
and both the request Host and direct peer are loopback. Detection performs no DNS lookup. The
application does not trust `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host`,
`X-Forwarded-Proto`, or similar headers by default.
The bundled Uvicorn startup explicitly disables its own proxy-header processing so it cannot rewrite
the peer or scheme before these application checks.

Logout revokes and deletes only the presented, active session after valid session CSRF. Expired,
revoked, disabled-user, deleted-user, and otherwise stale sessions are rejected, and their cookies
are cleared when the browser safely presents them.

Expired and revoked database rows are retained for seven days after expiry or revocation. Session
creation and revocation each inspect one serialized, fixed-size primary-key page and delete only
eligible rows in that page. A short final page resets the cursor immediately so newly inserted rows
cannot prevent wraparound. This bounds work per operation, guarantees rolling progress in the
single-process Community profile, and requires no background worker or schema change.

Browser login attempts are throttled in memory within each Community process. The defaults allow
20 attempts per direct source and five attempts per source/canonical-account pair in a five-minute
window. Wrong-password, unknown-account, inactive/deleted-account, and throttled requests all
return the same HTTP 401 response. A successful login clears only its source/account-pair state;
source-wide history remains. State expires, is capped at 10,000 source and pair entries combined,
and uses bounded cleanup plus deterministic least-recently-used eviction at the cap. This is
intentionally process-local: Community's single-process profile needs neither Redis nor distributed
coordination.

The account key is a fixed-size SHA-256 digest of the stripped, case-folded submitted identifier and
does not depend on an account lookup. The source is the direct peer by default. `Forwarded`,
`X-Forwarded-For`, `X-Real-IP`, and similar client-supplied headers are not trusted by default.
Browser identifiers are limited to 320 Unicode code points before canonicalization; longer values
share one constant malformed-account key and never reach authentication. Browser form bodies are
limited to 16 KiB so the framework does not buffer arbitrarily large login or setup submissions.

An operator may explicitly configure trusted proxy IPs or CIDR networks with repeatable
`--trusted-proxy IP_OR_CIDR` options or comma-separated `REFRNCR_TRUSTED_PROXIES`. Only
`X-Forwarded-For` from a configured direct proxy is considered. Every address must be a plain valid
IP address; the chain is examined from right to left and the first address outside the trusted
networks becomes the source. An untrusted direct peer, malformed/oversized chain, or chain containing
only trusted addresses falls back to the direct peer. `Forwarded` and `X-Real-IP` remain ignored.

Browser calls to `AuthenticationService` also pass through a process-local admission guard with a
capacity of four. Acquisition is non-blocking: a fifth concurrent verification immediately returns
the same generic HTTP 401 response without calling authentication or consuming limiter history.
Permits are released in a `finally`-equivalent context-manager boundary after success, credential
failure, or an exception. The guard covers only interactive browser authentication; CLI bootstrap
and `AuthenticationService` itself are unchanged. It has no waiting queue or distributed state.

Anonymous requests to registered protected browser endpoints redirect to `/login`; authenticated
permission failures still return 403. Static assets, unknown routes, setup, logout and FastAPI
health/API routes retain their separate contracts. A safe originally requested GET/HEAD path
travels through a hidden `next` field and is revalidated on POST. Other methods return to `/`
after login and never replay a mutation. Return targets must be root-relative paths: URLs,
protocol-relative paths, backslashes, controls/whitespace, dot segments, nested percent encoding,
query strings and fragments are rejected. Login, logout and setup targets also fall back to `/`.
Query parameters are deliberately not copied from protected requests, avoiding propagation of
credentials or other sensitive parameters. The destination still enforces its own authorization.

All registered browser state-changing methods (`POST`, `PUT`, `PATCH` and `DELETE`) are checked
for CSRF before the route runs. This check is based on the HTTP method and the active session (or
the anonymous setup/login cookie), not on an endpoint-name allowlist. Browser authorization uses
named capabilities at the route/service boundary; dataset mutations use
`can_maintain_dataset`, while installation administration uses
`can_administer_installation`/`can_manage_access`.

Missing login fields receive associated English validation errors (400), independent of any
account lookup, and still consume failed-attempt limiter history. Credential/account failures
and throttled attempts continue to share the generic 401 response. Only the escaped submitted
email (bounded to 320 code points) and request CSRF/return-path values may differ; passwords
are never supplied to the rendering context.

## Records query privacy (REF-136)

Records responses, including invalid-criteria responses, use `no-store` and `no-referrer`.
The normal CLI server redacts Records querystrings from its Uvicorn access handler before
formatting; validation/errors never include submitted values. Configure any independent
hosting access log or reverse proxy to omit these querystrings too. Criteria intentionally
live in shared URLs and browser history; they confer no Dataset or version authorization.
Concept routes retain server-side maintenance enforcement, while each service read resolves
current permissions and the selected content within one existing coherent snapshot.

## REF-40 API keys

API keys do not establish browser sessions. Management requires the existing active session and CSRF checks. The one-time creation response is non-cacheable and carries no-referrer policy; signed form identity prevents secret redisplay on replay. See [one-time display and pepper configuration](api-keys.md).
# Security audit boundary

Rejected persisted sessions, failed credential verification, denied authorization, failed CSRF
checks and rejected signed review/import workflow identities are recorded in the existing
administrator-only Audit history. Events contain no cookie, session ID, CSRF value, password,
submitted account identifier, client IP, raw path or request data. Ordinary required-field and
other non-security form validation remains outside the security audit.

## Browser response policy

Flask HTML responses, including login, redirects and error pages, enforce a same-origin
Content Security Policy. Scripts, styles, fonts and connections use local assets; images
also allow data URLs. Inline scripts, inline handlers, inline styles, embedded objects,
base-URL overrides and framing are prohibited. Forms submit to the same origin.
`X-Frame-Options: DENY` supplements `frame-ancestors 'none'`. HTML responses also disable
camera, microphone and geolocation through Permissions-Policy and use
`Cross-Origin-Opener-Policy: same-origin`. The default Referrer-Policy is `same-origin`;
existing `no-referrer` rules for sensitive pages remain stricter.

Every Flask response, including downloads, static assets and conditional responses, sends
`X-Content-Type-Options: nosniff`. Every HTTPS Flask response sends
`Strict-Transport-Security: max-age=31536000`, without `includeSubDomains` or `preload`.
Loopback HTTP does not send HSTS. Scheme detection uses the existing trusted request
scheme; forwarding headers do not enable HSTS. If TLS terminates at a proxy and the
application sees HTTP, the proxy must supply HSTS on its HTTPS responses. Transport and
proxy deployment guidance belongs to REF-46; this policy does not enable proxy-header
trust or TLS termination in the application.

The separately mounted API and API documentation retain their own security headers and
CSP. No API response contract or authentication/authorization rule changes.

## CSP verification

`tests/test_web_security_headers.py` verifies real responses and guards production
HTML templates against inline scripts, event handlers, style attributes and style blocks.
The mandatory `browser-csp` CI job runs a pinned Playwright Chromium smoke test. It exercises
login, external scripts/styles, keyboard controls and form submission, checks that normal
navigation raises no CSP violations, and verifies that inline script/style injection and
framing are blocked. Keep this job required in branch protection alongside the existing CI
checks when enabling it on GitHub.

Install browser-only test dependencies and run the same smoke test locally:

```shell
npm ci --ignore-scripts --prefix tests/javascript
node tests/javascript/node_modules/playwright/cli.js install chromium
.venv/bin/python tests/run_browser_tests.py web_csp
```

Run all existing acceptance scripts against fresh disposable, loopback-only applications:

```shell
.venv/bin/python tests/run_browser_tests.py --all
```

These acceptance scripts share a CSP violation guard. Test assets use same-origin URLs;
optional axe accessibility injection also uses a same-origin intercepted test URL, without
disabling CSP. A specific existing Playwright installation or Chromium executable can be
selected with `REFRNCR_PLAYWRIGHT_MODULE` or `REFRNCR_BROWSER_EXECUTABLE`. Playwright is a
test-only Node dependency and is not included in the Python runtime package.
