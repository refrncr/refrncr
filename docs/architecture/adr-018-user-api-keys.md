# ADR-018: User API keys

Status: accepted for REF-40 implementation; local review and independent CI pending.

## Context

REF-40 supplies Community/core credentials for the future REF-107 API. Existing
services resolve current user permissions. REF-149 requires real audit actors,
while unknown credentials cannot identify an actor reliably.

## Decision

Use an independent API-key service, persistence port and SQLAlchemy adapter.
Credentials carry a public UUID and 256 random bits; store only an HMAC-SHA256
verifier with a separately configured installation pepper. Return the secret
only from successful creation. Authentication returns the current owner ID;
authorization continues through existing services, without stored permission scopes.

Serialize creation and revocation on the owner row (SQLite uses its existing
serialized-write transaction). REF-169 supersedes owner-row authentication locking:
authentication locks only the key for revocation ordering and conditionally refreshes
`last_used_at` at most once per 15 minutes. Every lifecycle mutation and its audit commits
together; successful authentication has no durable per-request audit.

The product owner explicitly approved a narrow REF-149 exception: only
`api_key.authentication_failed` may have a null actor. Enforce the exception in
the audit contract and database constraint. Its object UUID identifies an attempt,
not a claimed key; show the actor as `Unauthenticated`, without a user link.
Other events still require an existing user. No request content is audited.

## Consequences

Production must configure and protect the pepper independently of the database.
Pepper replacement invalidates existing credentials. Authentication conditionally writes an
approximate last-used timestamp, so database availability remains part of authentication
availability without per-request audit growth or owner-lock contention. A request authenticated before a revoke
commits may finish; subsequent authentication fails. Populated downgrade is
refused to preserve credentials and audit history.

## Alternatives

A plain hash lacks the required server-side secret. Reversible encryption would
permit secret recovery. An external secrets service adds unnecessary Community
infrastructure. A fabricated audit user misrepresents unknown actors. Persisted
permission scopes would diverge from current user authorization.

See [operation and integration details](../development/api-keys.md).
