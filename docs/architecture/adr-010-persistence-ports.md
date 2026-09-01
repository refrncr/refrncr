# ADR-010: Use tenant-agnostic persistence ports for shared services

Status: Accepted

## Context

The Community User, Team, Membership, and Grant services combined business rules with direct
SQLAlchemy session, query, locking, and model-construction operations. The authorization service
also acquired permission data and evaluated it in the same implementation. A Cloud deployment
needs to reuse these rules while supplying persistence that enforces its own installation context.

Community remains single-tenant per installation. Tenant identifiers and Cloud persistence models
do not belong in the shared service contracts or Community database schema.

## Decision

Shared mutation services depend on a tenant-agnostic unit-of-work factory with focused User, Team,
Membership, and Grant operations. A use case enters one unit of work, and all operation groups and
audit insertion share its transaction. Operation groups never commit independently.

The Community adapter implements the unit of work with one SQLAlchemy session and retains the
existing SQLite and PostgreSQL locking behavior. It constructs the existing Community mapped
models and translates flush-time integrity conflicts into a persistence-neutral error.

Authorization data acquisition uses a separate `PermissionDataSource`. Permission ranking and
access evaluation are pure, tenant-agnostic functions shared by every deployment. The existing
`ResourceTypeRegistry` remains the resource-existence boundary, and the Community administrator
override remains separate from Grant-derived permissions.

`SessionService` remains SQLAlchemy-based. Only active-session revocation needed by User lifecycle
operations is available through the unit of work so the mutation and revocation remain atomic.

## Consequences

Community behavior and schema remain unchanged. Community composition selects the SQLAlchemy unit
of work and permission data source explicitly. A future Cloud composition can provide adapters
that capture tenant context internally without adding tenant parameters or fields to shared core.

The shared services use structural record contracts rather than requiring Cloud persistence to
return Community mapped classes. A complete domain/ORM model split is therefore unnecessary.

Adapter implementations carry database-specific query, constraint, transaction, and locking
responsibilities. Contract and integration tests are required to protect those boundaries.

## Alternatives considered

- Extract only permission reads. This would preserve shared authorization semantics but leave all
  mutation services coupled to Community persistence.
- Add generic repositories. This would expose more CRUD surface than current use cases require and
  obscure transaction ownership for audit, cleanup, and session revocation.
- Subclass the current services. Nearly every method would need replacement because persistence
  operations were interleaved with business rules, duplicating the rules in practice.
- Use SQLAlchemy query hooks as the extension mechanism. Hooks would not address model construction
  and would make application isolation implicit and easy to bypass.
- Split all domain and ORM models. The focused record and adapter boundaries provide the required
  extension point without that larger redesign.
