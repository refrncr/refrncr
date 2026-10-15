# ADR-009: Compose Flask web UI inside a FastAPI ASGI application

Status: Accepted

## Context

REF-33 establishes Flask for the initial server-rendered interface and FastAPI for HTTP APIs.
Business and authorization rules must be shared and framework-independent.

## Decision

FastAPI is the process-level ASGI application. API routes are registered on it, and the Flask
WSGI application is mounted at `/` after those routes. Both boundaries receive the same
application services and session factory through explicit construction.

The composition root bootstraps exactly one Community `Organisation`. Application services run
inside that installation context and therefore do not accept organisation or tenant identifiers.
Users, sessions, and audit events contain no generic tenant-scoping columns. Cloud provisioning
and tenant isolation are separate concerns of `refrncr-cloud`, not extension requirements imposed
on the Community database model.

The domain and application layers import neither framework. REF-33 does not introduce a public
user-management REST contract; future FastAPI endpoints will call the same services as Flask.

## Consequences

The product has one deployable application and one composition root. Flask can deliver the first
web experience without duplicating business logic, while FastAPI remains available for explicit
API stories. The WSGI/ASGI bridge adds a boundary that must be retained or deliberately replaced.
The Community service interfaces remain small and installation-scoped; a Cloud integration must
adapt or compose them externally rather than turning the core schema into a multi-tenant schema.

## Alternatives considered

- Run independent Flask and FastAPI processes, which would duplicate deployment configuration.
- Implement user-management rules in Flask views, which would prevent safe reuse by FastAPI.
- Add a user-management API now, which would invent a contract not required by REF-33.

## REF-107 implementation

The product owner reconfirmed this composition on 2026-09-19. The existing Flask
browser app, WSGI adapter and CLI startup remain. A dedicated FastAPI module mounts
at `/api/v1` before the `/` Flask mount. REF-109 registers public `/api/openapi.json` and `/api/docs` routes on the ASGI root
before the Flask mount, generated from the mounted API and documentation routes. [ADR-019](adr-019-current-rest-reads.md) records shared read ports,
canonical pagination, credentials and private conditional-response choices.

## REF-70 operational boundary

The same FastAPI root owns `/healthz`, `/readyz` and request-correlation/
operational logging middleware before every mounted boundary. Liveness is process
only; runtime readiness uses the composition's existing SQLAlchemy engine and the
packaged Alembic script head. Neither probe enters OpenAPI or durable domain audit.
The Flask mount, API mount and CLI's automatic fail-closed migration/bootstrap
sequence remain unchanged. REF-168 owns any future migration/start separation.

## Current deviations

The Flask boundary currently contains request-specific orchestration and presentation mapping for
server-rendered forms, including parsing request data, selecting the response context and
translating service errors into form responses. This is intentional composition code at the web
edge, not a second business-logic layer: authorization, domain validation rules, transactions and
persistence remain in the shared application services. FastAPI endpoints must continue to call
those same services and may use their own API-specific mapping.

A future extraction is appropriate only when a separate story defines a reusable API contract or
another concrete consumer. This ADR does not require a speculative `web.py`/services split as
cleanup work.
