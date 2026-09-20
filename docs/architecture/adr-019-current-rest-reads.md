# ADR-019: Current REST reads in the existing ASGI composition

Status: local REF-107 implementation; independent CI and merge pending.

## Context

REF-107 exposes Community/core Current publications using REF-40 credentials.
The product owner confirmed on 2026-09-19 that ADR-009's existing FastAPI root
and Flask browser app remain authoritative. There is no Starlette browser migration.
ADR-010 owns ports/UoW, ADR-011 Dataset authority, ADR-013 canonical datatypes,
ADR-014 publication atomicity, ADR-017 independently mutable Dataset metadata,
and ADR-018 API-key authentication. REF-109 owns public documentation delivery.

## Decision

Mount the dedicated FastAPI HTTP module at `/api/v1` before the unchanged
`a2wsgi.WSGIMiddleware` root mount. Pydantic models describe the additive v1 JSON
contract. REF-109 publishes its generated schema and local Swagger UI at
`/api/openapi.json` and `/api/docs` on the ASGI root. Existing CLI
startup continues to serve the same ASGI app.

Extend `DatasetVersionService` and its existing version operations port/adapter.
Authentication commits its existing audit separately; all content selection,
current Dataset authorization, conditional validation and bounded content reads
share the existing explicit SQLite/repeatable-read PostgreSQL snapshot. The bulk
view operation belongs to the existing Dataset authorization service. Active
Community users read all Current Datasets under REF-73; no legacy Grant policy,
per-key scope, Cloud tenancy or parallel business layer is introduced.

SQL selects one eligible non-cancelled Current version per Dataset and paginates
Datasets by key. Record SQL orders correlated canonical identifier components in
Field-key order, retaining the existing typed ordering and internal UUID tie-break.
PostgreSQL uses exact NUMERIC and C collation; SQLite uses an exact Decimal text
collation rather than lossy NUMERIC affinity, and BINARY for other canonical text.
UTC suffix removal preserves fractional-second Timestamp order. SQL LIMIT/OFFSET
selects at most 100 Record identities before one joined page-value read. Existing
indexes remain: this does not promise indexed arbitrary typed ordering or constant
CPU at large offsets. REF-164 owns broader indexing/scaling.

The opaque public identifier is unpadded base64url of the complete existing
length-prefixed canonical business key (maximum 2,000 bytes), never the internal
Record UUID. It is reversible transport encoding, not encryption, a hash, a label,
or a replacement canonicalization algorithm. Clients treat it as opaque and URL
encode the complete returned string. Single/composite identifiers use one contract.

Strong SHA-256 ETags bind the serialization contract, authenticated owner, selected
representation/pagination, total, Current version identities and actual current
Dataset metadata. The metadata contribution is necessary under ADR-017: changing
name/description/icon must not reuse an ETag. Authorization and Record existence
precede conditional evaluation. A matching ETag avoids Field/Record-value loading.
Private revalidation is mandatory on every use; `Vary: Authorization, Accept-Encoding`
prevents credential/encoding variants sharing a cache entry.

Last-Modified is the selected publication time (maximum on the selected Dataset
page; Unix epoch for an empty page). If-None-Match takes precedence, including when
malformed/nonmatching. Date-only validation is conservative: collections cannot
prove membership/deletion unchanged; mutable metadata, scheduled activation and
subsecond changes must not produce a false 304. Dates that cannot prove freshness
return 200. See the [HTTP contract](../development/rest-api.md).

## Consequences

No migration, stored sort key, second persistence path, new domain event, browser
rewrite or additional process. Pydantic becomes an explicit existing-transitive
dependency; FastAPI/Pydantic are constrained to tested minor lines. The read API
still incurs REF-40 authentication audit writes and owner-row locking per request,
including a 304. A publication/access change after a read snapshot begins affects
subsequent reads; in-flight coherent reads may finish.

Database sorting can scan/sort identifiers; Python memory holds schema and one page,
not all Records. Offset pages across separate requests are not a pinned multi-request
snapshot. Consumers can detect publication changes through version numbers/ETags.
Date validators deliberately optimize less often than ETags, particularly because
Dataset `updated_at` also changes during lifecycle operations. No narrower metadata
revision or publication-time rewrite is introduced merely for cache efficiency.

## Alternatives considered

- Replace Flask or add a second service: unnecessary and explicitly rejected.
- Call the existing browser Record-page operation: materializes/sorts the full set.
- Sort encoded binary identifiers: incorrect numeric and length-prefix ordering.
- Add persisted ranks/indexes now: migration and backfill beyond the required bounded read.
- Derive ETag solely from publication UUID: stale independently editable metadata.
- Return 304 before authorization/existence: discloses/prolongs revoked access.
- Use presentation labels or Record UUIDs: ambiguous composites or internal identity leak.


## REF-109 documentation delivery

The root composes the mounted API's actual generated schema with the two public
FastAPI documentation operations, prefixing data paths by the existing `/api/v1`
mount. No data/service calls enter schema generation. The exact Decimal serializer
now derives from JSONResponse so FastAPI recognizes its media type and generates
response-model references; its renderer and data contract are unchanged.

Swagger assets are pinned, licensed and served by the existing Flask static route.
A standalone packaged HTML document avoids a browser session dependency. Its scoped
styles reuse tokens; its external initializer disables persistence, external
validation, URL configuration and credential-bearing snippets. No CSP unsafe-inline
or unsafe-eval exception is added. Raw JSON remains the accessible/no-JavaScript
alternative. See the REF-109 report and REST guide for validation and limitations.
