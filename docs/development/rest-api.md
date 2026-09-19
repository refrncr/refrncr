# Current published REST API (REF-107)

This is a read-only Community/core interface at `/api/v1` in the existing
FastAPI + Flask deployment. Enable REF-40 API-key configuration as described in
[API keys](api-keys.md). Flask routes, sessions, CSRF and static assets keep their
existing contracts. Public OpenAPI/Swagger delivery belongs to REF-109 and is disabled;
`create_api(...).openapi()` generates the source schema for that follow-up.

## Endpoint matrix

| GET path | `data` | Additional envelope |
| --- | --- | --- |
| `/api/v1/datasets` | Array of Dataset summaries | `pagination`, `links` |
| `/api/v1/datasets/{dataset_key}` | Dataset summary plus ordered `fields` | None |
| `/api/v1/datasets/{dataset_key}/records` | Array of Records | `current_version`, `pagination`, `links` |
| `/api/v1/datasets/{dataset_key}/records/{identifier}` | One Record | `current_version` |

A Dataset summary has `key`, `name`, `description`, `icon` (nullable), and
`current_version` (`number`, `published_at`, canonical UTC timestamp).
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

Collections accept only `offset` (default 0, 0..2147483647) and `limit` (default 50,
1..100). Values must be ASCII whole decimal numbers in canonical notation; signs,
leading zeroes except `0`, fractions/exponents, duplicate and unknown parameters fail.
Querystrings are bounded to 256 bytes. Detail/lookup endpoints accept no query fields.
`pagination` contains `offset`, `limit`, `total`; `links` contains `self`, `next`,
`previous`, with nullable next/previous. Links are application-relative paths built
from validated keys and integers, never Host, forwarded headers, credentials or
arbitrary supplied querystrings. Reverse proxies should preserve the `/api/v1` prefix.

Dataset order is key ascending. Records retain typed canonical identifier order and
stable internal tie-breaking. Offset beyond the result set succeeds with an empty
array and the correct total. Only Current content is available: no Concept,
Scheduled, Expired, Cancelled or fallback working copy. Unsupported search, filters,
sort, version/effective-date selection, writes/imports/exports/mappings/hierarchies
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

The normal CLI Uvicorn access handler replaces API targets with `/api/[redacted]`.
Configure reverse proxies, independent ASGI hosting and observability to omit raw
Authorization, API paths/querystrings, request/response content and SQL bind/results.
The HTTP boundary consumes unexpected exceptions without exposing exception payloads.
REF-40 authentication continues its existing payload-free audit and last-used write;
ordinary data reads/304 add no domain event. No new rate limiter, telemetry or security
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
requests can cross publications; inspect `current_version` and restart when needed.

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
with public documentation disabled until REF-109.
