# ADR-023: UTC-aware database timestamps and a shared application clock

Status: accepted for REF-222 implementation; GitHub CI remains the independent pre-merge gate.

## Context

SQLite returns timezone-naive values for `DateTime(timezone=True)` while PostgreSQL returns
aware values, leaving application code to guess whether a value should be interpreted as UTC.
Independent `datetime.now()` calls and service-specific clock callables also make timestamp
behavior inconsistent and difficult to test deterministically.

## Decision

Use one SQLAlchemy `UTCDateTime` type for every ORM timestamp column. Bind values only when
timezone-aware, normalize them to UTC, and return aware UTC values on both supported databases.
The decorator preserves the underlying `DateTime(timezone=True)` schema, so no migration is
required.

Compose one application `Clock` through services and the Unit of Work. Its `now()` returns an
aware UTC instant and its `today()` returns the date in `APPLICATION_TIMEZONE` (UTC). The Unit
of Work supplies persistence timestamps, and an ORM flush listener supplies timestamps for
ordinary ORM inserts and updates. Remove model-level timestamp defaults so persistence has one
clock source. Keep elapsed-duration timing (for example, monotonic transient import expiry)
separate from calendar and audit timestamps.

## Consequences

Naive timestamps fail at database binding rather than being silently reinterpreted. SQLite and
PostgreSQL now expose the same aware UTC contract, while existing data, SQL column types and
migrations remain unchanged. Tests can inject a fixed clock and verify business time, ORM
timestamps and audit events together. HTTP-date parsing and local calendar source-data parsing
remain separate explicit boundary contracts.

## Alternatives

Keeping backend-specific conversions duplicates timezone policy across services and adapters.
Attaching UTC to every naive datetime at arbitrary ingress silently accepts ambiguous input.
Changing stored column types or rewriting existing rows would require unnecessary migrations and
would not fix independently sampled clocks.

See [Dataset version dates](../development/dataset-versions.md),
[Dataset context persistence](../development/dataset-context.md), and
[Audit history](../development/audit-history.md).
