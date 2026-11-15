# ADR-026: Domain packages and one integration boundary

Status: Accepted

## Context

The Community application has a shared service and persistence core, but most of that core is
still arranged as flat modules. `services.py` contains twelve services, other modules import
private helpers from it, local imports hide dependency cycles, and external integrators import
several internal modules directly. This makes ownership and safe change boundaries difficult to
see.

[ADR-009](adr-009-flask-fastapi-composition.md) establishes one FastAPI/Flask application and
[ADR-010](adr-010-persistence-ports.md) establishes persistence-neutral ports with Community
SQLAlchemy adapters. This decision makes those boundaries visible in the package layout without
changing behavior, database models, routes, or API contracts.

## Decision

### Target package layout

Domain packages contain their services, domain-specific errors and contracts. Neutral port
contracts remain in `ports`; Community SQLAlchemy implementations are centralized in
`persistence`. The following table is the target for every currently flat production module.
Names within a package may be made more specific while moving, but responsibility may not move to
a different layer without a follow-up ADR.

| Target | Current flat modules or responsibility |
| --- | --- |
| `identity/` | identity parts of `services.py`; `api_keys.py`; `login_security.py`; `argon2_benchmark.py` |
| `authorization/` | authorization, grant and membership parts of `services.py`; `authorization.py`; `installation_policy.py`; `installation_settings.py` |
| `datasets/` | Dataset, field, business-key and record parts of `services.py`; `datatypes.py`; `field_contracts.py`; `record_identity.py`; `mapping_updates.py`; `dataset_diff.py`; `dataset_export.py`; `record_search.py` |
| `versions/` | `dataset_versions.py`; `version_contracts.py`; `published_contracts.py`; `record_intervals.py`; `review_tokens.py`; `diff_classification.py`; `diff_contracts.py`; `change_status.py`; `concept_contracts.py` |
| `imports/` | `record_import.py`; `source_formats.py`; `upload_names.py`; `xml_security.py` |
| `hierarchies/` | `hierarchy.py`; `hierarchy_contracts.py`; `hierarchy_import.py` |
| `persistence/` | `persistence.py`; `models.py`; `api_key_persistence.py`; `concept_persistence.py`; `diff_persistence.py`; `hierarchy_persistence.py`; `overview_persistence.py`; `version_persistence.py`; the Community database adapter responsibility in `db/session.py` |
| `audit/` | `audit.py`; `audit_guard.py`; `audit_history.py`; `audit_presentation.py` |
| `presentation/` | `presentation.py`; `presentation_metadata.py`; `overview.py` |
| shared domain/application modules | the category bases in `errors.py`; `domain.py`; `clock.py`; `ports.py` |
| composition and operations | `app.py`; `cli.py`; `operational.py`; `browser_security.py` moves to `web/security.py` |
| one-off upgrade support | `concept_upgrade.py`, `dataset_upgrade.py` and `version_upgrade.py` move beside their owning domain; Alembic code remains under `migrations/` |

Existing `api/`, `web/`, `db/` and `migrations/` packages keep their framework, database setup and
migration responsibilities. `services.py` is a temporary compatibility facade: during the
migration it re-exports every name moved out of it. Internal imports are changed to the owning
package by REF-370; that subtask also adds the facade's `DeprecationWarning`. REF-353 removes the
facade and the `UserManagementError` compatibility alias only after Cloud has migrated.
Domain markers and specific errors move from the transitional shared `errors.py` into each
domain's `errors.py`; only `DomainError` and the four category bases remain shared.

### Dependency rules

The allowed direction, later enforced by import-linter, is:

1. `web/` and `api/` may import application/domain packages and neutral contracts.
2. Domain packages may import shared domain modules, other explicitly required domain contracts,
   and `ports`; they may not import `web`, `api`, `persistence` or the composition root.
3. `persistence/` implements `ports` and domain contracts. It may not be imported by a domain
   package and is connected only by the composition root.
4. `app.py` is the only composition root and may import every layer needed to construct the
   application. `cli.py` calls that composition rather than building an alternative object graph.
5. `refrncr.integration` may import internal modules to expose its small public contract. No
   production module inside `refrncr` may import `refrncr.integration`.
6. `web/` and `api/` do not import each other. Both use the same application services.
7. Imports inside functions are not used to evade these rules or break cycles.

Within a domain package, service modules may depend on that package's contracts and errors. A
module-private function stays inside its defining module. Cross-module behavior is given a public
name even when it remains internal to the installed distribution. A static guard checks this rule
for production code under `src/refrncr`, excluding Alembic migrations.

### Composition root and integrator wrappers

Only `app.py` constructs adapters and services. `create_flask_app` receives the already composed
services and constructs Flask-only concerns: blueprints, CSRF, browser contexts and the
application-instance security-audit callables described by ADR-009. It does not create a fallback
domain service or persistence adapter.

The composition API will accept one documented, immutable wrapper configuration with two optional
callables: a user-service wrapper and a dataset-service wrapper. Each callable receives the fully
constructed Community service and returns an object implementing the same public service contract.
Composition applies wrappers once, replaces the corresponding exposed service in the service
bundle, and then supplies that final bundle to Flask and FastAPI. Wrappers do not receive sessions,
repositories or Cloud state from Community; an integrator closes over its own policy state. This
allows Cloud to apply product limits without subclassing Community services or changing the
Community schema. A wrapper-raised `ConflictError` follows the existing boundary error handling.

REF-362 implements the hook and its contract test. This ADR fixes its shape and ownership; it does
not add the hook during REF-355.

### Public integration contract

`refrncr.integration` is the only supported import surface for code outside this distribution. It
will expose exactly the production needs recorded by REF-356:

- `create_application` and `create_community_services`;
- `create_database_engine` and `create_session_factory`;
- `prepare_database`, `database_is_ready` and `expected_database_revision`;
- `create_initial_admin`, `CreateAdminConfiguration` and `CliError`;
- the supported administrator-password recovery operation, without exposing
  `SqlAlchemyUnitOfWorkFactory`;
- `ResourceTypeRegistry`;
- `NotFoundError`, `ConflictError`, `ValidationError` and `AuthorizationError`.

REF-356 creates this package, contract tests and `docs/development/integration.md`. The integrator
wrapper hook is added to the same contract by REF-362. The Cloud beta-seed replacement is separate
work in REF-354 and is not part of this package's initial implementation.

### Domain error hierarchy

`DomainError` is the safe application-boundary base. Each domain has a marker:
`IdentityError`, `AccessManagementError`, `DatasetError`, `VersionError`, `ImportDomainError` and
`HierarchyError`; persistence-neutral failures that are safe to expose may use `PersistenceError`.
The access-management name keeps the domain distinct from the `AuthorizationError` category. The
import marker is deliberately called `ImportDomainError` to avoid shadowing Python's built-in
`ImportError`.

Every specific domain failure ultimately combines one domain marker with exactly one semantic
category:

- `NotFoundError` for absent or deliberately concealed resources;
- `ConflictError` for a request that conflicts with current state;
- `ValidationError` for invalid input;
- `AuthorizationError` for denied access, mapped to HTTP 403 and
  `security.authorization_denied` at the browser boundary.

For example, a Dataset-specific missing-resource error inherits from both `DatasetError` and
`NotFoundError`. Boundary code maps categories and does not need to know every domain-specific
type. `UserManagementError` is an alias of `DomainError` during the compatibility period; it is not
a separate superclass. Existing imports from `refrncr.services` continue to resolve through that
module's re-exports.

### Size and private-function guards

The final limit is approximately 800 lines for every Python file below `src/refrncr`. Alembic files
under `src/refrncr/migrations/` are the only size exception because they are immutable,
independently ordered migration artifacts. There are no other size exceptions in this decision.

There are no exceptions to the private-function rule at acceptance of this ADR. Framework
callbacks may keep a leading underscore while they remain inside their defining module; if another
module needs them, they receive a public name. Any future exception requires an ADR update with a
narrow reason before the guard is weakened.

## Consequences

The eventual package tree makes domain and infrastructure ownership visible, and import-linter can
enforce the same direction developers see. Cloud receives one releaseable public contract while
Community retains freedom to reorganize internal modules. Category-based errors allow web, API and
integrator boundaries to translate failures consistently.

The migration needs a temporary facade and compatibility alias. Some modules will briefly import
new packages while other responsibilities still live in flat modules. Each subtask must preserve
behavior and reduce complexity without broadening the public contract.

## Alternatives considered

- Keep one large `services.py` and document sections. This would not establish enforceable package
  boundaries or remove cycles.
- Put SQLAlchemy adapters inside every domain package. This would obscure the single Community
  adapter layer selected by ADR-010 and make composition dependencies easier to reverse.
- Make all existing modules public. That would freeze accidental internal contracts and leave
  Cloud coupled to Community implementation details.
- Let integrators subclass services or replace repositories. Subclassing duplicates business
  rules, while repository access bypasses application validation, audit and transaction ownership.
- Use Radon for the refactor. Ruff C90 is already in the required toolchain and is the canonical
  complexity gate for this work.
