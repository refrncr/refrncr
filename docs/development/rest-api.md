# Current published REST API (REF-107)

This is a read-only Community/core interface at `/api/v1` in the existing
FastAPI + Flask deployment. Enable REF-40 API-key configuration as described in
[API keys](api-keys.md). Flask routes, sessions, CSRF and static assets keep their
existing contracts. Public documentation is available at `/api/docs`; `/api/openapi.json` exposes the
OpenAPI 3.1 contract generated from the registered FastAPI routes and Pydantic models.

## Endpoint matrix

| GET path | `data` | Additional envelope |
| --- | --- | --- |
| `/api/v1/datasets` | Array of Dataset summaries | `pagination`, `links` |
| `/api/v1/datasets/{dataset_key}` | Dataset summary plus ordered `fields` | None |
| `/api/v1/datasets/{dataset_key}/records` | Array of Records | `version`, `pagination`, `links` |
| `/api/v1/datasets/{dataset_key}/records/{identifier}` | One Record | `version` |

A Dataset summary has `key`, `name`, `description`, `icon` (nullable), and
`version` (`number`, `published_at`, canonical UTC timestamp, and `effective_from`,
ISO calendar date) describes the selected immutable published version.
Name/description/icon are current Dataset metadata per ADR-017. Fields and Records
come exclusively from its immutable Current publication. No internal Dataset,
Field or Record UUID, publisher identity, release note or Scheduled end boundary is returned.

Field metadata, in published display order: `key`, `name`, `datatype`,
`is_identifier`, `is_required`, `is_unique`, `display_position`, `definition`, `unit`.
Definition/unit are nullable plain text. No output-format configuration exists yet;
the implemented required/unique/order metadata is retained without invented settings.

A Record is `{"identifier": "<opaque business identifier>", "values": {...}}`.
Every published Field key occurs in `values`; absent values are JSON null.
String and Boolean remain native; Integer, Decimal (`number`) and Float are JSON
numbers. Decimal uses exact canonical decimal tokens without a float intermediate.
Date, Time, DateTime and Timestamp use the existing canonical strings, Timestamp
in UTC with `Z`. Clients requiring exact large integers/decimals must use an exact
JSON number parser. Neither locale formatting nor source-format proposals apply.

The identifier encodes the full stored canonical length-prefixed business key
as unpadded base64url. It is **not secret** or an authorization token. Single and
composite keys use the same reversible encoding; composite components retain
existing Field-key order. Use the entire returned identifier, URL encoded, for lookup.
Do not reconstruct it from display labels, truncate it, or substitute an internal UUID.

## Pagination and validation

All endpoints accept `effective_date=YYYY-MM-DD` and `version=<positive number>`.
An effective date selects the latest non-cancelled publication whose `effective_from` is
on or before that date. A version selects that immutable publication, including a Scheduled
publication. When combined, the version must be effective on the date or the request returns
safe 400. A date before the first publication and an absent/cancelled version return safe 404.
Without either selector, Current selection is unchanged.

Collections also accept `offset` (default 0, 0..2147483647) and `limit` (default 50,
1..100). Values must be ASCII whole decimal numbers in canonical notation; signs,
leading zeroes except `0`, fractions/exponents, duplicate and unknown parameters fail.
Querystrings are bounded to 256 bytes.
`pagination` contains `offset`, `limit`, `total`; `links` contains `self`, `next`,
`previous`, with nullable next/previous. Links are application-relative paths built
from validated keys and integers, never Host, forwarded headers, credentials or
arbitrary supplied querystrings. Reverse proxies should preserve the `/api/v1` prefix.

Dataset order is key ascending. Records retain typed canonical identifier order and
stable internal tie-breaking. Offset beyond the result set succeeds with an empty
array and the correct total. No Concept, Cancelled or fallback working copy is available.
Unsupported search, filters, sort, writes/imports/exports/mappings/hierarchies
are not part of v1. Incompatible future changes require a new API version.

## Authentication, authorization, errors and privacy

Supply exactly one `Authorization: Bearer <api-key>` header. Browser sessions,
cookies and URL/query credentials never authenticate API requests. The key uses
its owner's current Dataset rights through `DatasetAuthorizationService` in the
content read snapshot. REF-73 currently permits every active, nondeleted Community
user to view all Current Datasets; losing maintenance/team/export access alone does
not remove read rights. Deactivating/deleting the key owner causes authentication
401. A Dataset read denied after authentication returns 404, including conditional
requests; no per-key scope or legacy Grant authority is introduced.

| Status | JSON `error` |
| --- | --- |
| 400 | `invalid_request` |
| 401 | `invalid_api_credentials` (also `WWW-Authenticate: Bearer`) |
| 404 | `not_found` |
| 405 | `method_not_allowed` |
| 503 | `service_unavailable` |

Missing, malformed, duplicate, unknown, expired and revoked credentials share the
401 body. Missing/invalid/inaccessible Dataset and Record targets share 404.
Operational authentication/audit/database failures fail closed as 503. Errors never
include submitted input, Record identifiers/values, headers, SQL or exception traces.
Responses set `nosniff`, `no-referrer`, frame denial and restrictive JSON CSP; no
CORS or browser session/CSRF bypass is added. Error responses are private/no-store.

The root ASGI operational middleware logs registered route templates rather than raw
API paths or querystrings and never logs Authorization or request/response content.
Configure reverse proxies and independent hosting to apply the same redaction boundary.
The HTTP boundary consumes unexpected exceptions without exposing exception payloads.
Successful API-key authentication creates no durable success-audit row. It marks only the
existing safe request-completion event with `authentication: "api_key"` and conditionally
refreshes `last_used_at` at most once per 15 minutes, including for 304 responses. Ordinary
data reads/304 add no domain event. No new rate limiter, telemetry or security
logging platform is claimed. Protect the existing pepper and use HTTPS externally.

## Conditional requests

Every successful response has a strong `ETag` and publication-based `Last-Modified`.
ETags bind Current version identities, representation, owner, pagination/total and
actual current Dataset metadata. A newly selected publication changes the ETag even
if the selected page's values happen to match. Metadata-only edits also invalidate it. Future serialization changes must advance
the internal ETag contract marker even when they are additive within v1.

Send `If-None-Match` with a previous tag. A match, weak match, matching tag in a list,
or `*` for an existing representation yields 304 with no body after authentication,
authorization and existence checks. The header is authoritative over
`If-Modified-Since`; invalid/nonmatching tags do not fall back to date validation.
A 304 retains ETag, Last-Modified, cache controls, Vary and security headers, while
avoiding Field and Record-value queries/serialization.

All successful/304 responses use
`Cache-Control: private, no-cache, max-age=0, must-revalidate` and
`Vary: Authorization, Accept-Encoding`. A private cache must revalidate before every
reuse; shared caching is forbidden. Errors use `private, no-store`. No cookies are
issued by the API. The owner contribution also makes tags distinct across users.

Last-Modified is the Current publication timestamp, truncated for HTTP-date precision;
Dataset lists use the maximum on the page, or Unix epoch if the page is empty.
An initial request requires no date. Date-only validation is conservative: Dataset
collection membership/deletion cannot be proven with publication timestamps, so
collections use 200 unless an ETag matches. For details/Records, the supplied valid
nonfuture date must cover exact publication time, current metadata `updated_at` and
Current's activation date. Same-second/subsecond changes therefore fall back to 200;
echoing a second-precision Last-Modified need not yield 304. Prefer ETags for reliable
revalidation. This avoids stale metadata or Scheduled activation being masked by an
older publication timestamp.

## Performance and operation

Dataset collections use a Current-window query with SQL count and page LIMIT/OFFSET.
Record collections select at most `limit` internal identities in SQL, then load only
those identities in one joined values/Fields query. Lookup uses the existing unique
`(version_id, identifier_key)` index. Content/authorization share one repeatable read;
concurrent publication returns entirely the old or new version. Separate pages across
requests can cross publications; inspect `version` and restart when needed.

No schema/index migration is introduced. PostgreSQL exact NUMERIC/C ordering and
SQLite's exact-number/BINARY collation reproduce existing Record order without full
Python materialization. Correlated identifier lookups and sorting can still scan
candidate identifiers, and large offsets cost more. Field metadata and page values
consume O(schema + page values) Python memory. This is bounded loading, not a claim
of constant database CPU or memory. REF-164 owns broader query/index scaling.

See [ADR-019](../architecture/adr-019-current-rest-reads.md) and
[the validation/review report](ref-107-report.md).

HTTP validator precedence and weak comparison follow
[RFC 9110 conditional requests](https://www.rfc-editor.org/rfc/rfc9110.html#name-conditional-requests).
Composition uses FastAPI's supported
[sub-application mount](https://fastapi.tiangolo.com/advanced/sub-applications/)
with public documentation provided by REF-109.


## Public OpenAPI and Swagger (REF-109)

`GET /api/openapi.json` and `GET /api/docs` are anonymous read-only documentation
routes. The document includes the four data operations above and these two
public documentation operations; Flask, health, internal and unimplemented routes
are excluded. OpenAPI is generated in memory from actual registered FastAPI routes
and the existing Pydantic models, with public mount paths added. There is no stored
or manually duplicated specification. Framework-generated 422 documentation is
removed because the existing boundary translates validation failures to safe 400.

Choose **Authorize**, enter the key alone, then use **Try it out** and **Execute**.
Only the Authorization header carries the key. Swagger omits browser cookies,
persists no authorization, disables URL-based configuration/deep links, and clears
its in-memory authorization and UI on pagehide. Reload or leaving the page clears
it; history restoration reloads a clean page. Curl/request-snippet components are
removed because upstream includes the Authorization header in them. No raw key
is embedded in generated HTML, examples, schema, URLs, browser storage or audit.
The browser necessarily holds a user-entered key in memory while authorizing.
Successful Try it out responses contain authorized data; clear them by leaving.

Swagger UI 5.33.0 assets and licenses ship locally with the wheel, pinned and
hash-recorded in [asset provenance](../../src/refrncr/static/licenses/ASSET-SOURCES.md).
There is no CDN, online validator, telemetry integration or new Python/Node runtime
dependency. The standalone page permits same-origin scripts/styles/fonts/connections
under CSP without unsafe-inline or unsafe-eval; embedded Swagger SVG images need
`img-src data:`. Existing JSON and Flask policies remain unchanged. Documentation
responses use no-store, no-referrer, nosniff and frame denial. Deployment must
continue redacting API targets and Authorization in external hosting/proxy logs.

The raw JSON link is always available, including without JavaScript. Swagger has
a page title, main landmark, skip link, visible focus, a labelled password input,
authorization-dialog focus containment and Escape/focus restoration. Scoped CSS
reuses shared tokens and wraps narrow content at 320/768/1440 px. Exact evidence
and upstream accessibility limits are in [the REF-109 report](ref-109-report.md).

Configuration follows the upstream [Swagger UI configuration reference](https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/).

Health/readiness are operational deployment surfaces, not data-API operations, and
remain outside OpenAPI. See the [runtime operations guide](operations.md).
