# Integrating with refrncr Community

`refrncr.integration` is the supported import surface for software that embeds the Community
application. Other `refrncr` modules are implementation details and may move as the domain-package
migration proceeds.

## Application composition

- `create_community_services(session_factory, resource_types, *, clock=..., wrappers=...)`
  constructs the Community service bundle with the built-in SQLAlchemy adapters.
- `ServiceWrappers(users=..., datasets=...)` supplies immutable, optional wrapper callables. Each
  receives one fully composed Community service and returns the service contract exposed to the
  application. Wrappers are applied exactly once; close over integrator policy state instead of
  expecting Community sessions or repositories.
- `create_application(services, ...)` constructs the combined FastAPI and Flask application from
  the complete bundle returned by `create_community_services`. Adapters never construct missing
  services or recover them through private attributes.
- `ResourceTypeRegistry` registers existence checks for authorization resource types.

```python
from refrncr.integration import (
    ResourceTypeRegistry,
    create_application,
    create_community_services,
    create_database_engine,
    create_session_factory,
)

engine = create_database_engine("postgresql+psycopg://...")
services = create_community_services(
    create_session_factory(engine),
    ResourceTypeRegistry(),
)
application = create_application(services, readiness_engine=engine)
```

For example, an integrator can apply product policy without subclassing Community services:

```python
from refrncr.integration import ServiceWrappers, create_community_services

services = create_community_services(
    session_factory,
    resource_types,
    wrappers=ServiceWrappers(
        users=lambda community: LimitedUsers(community, limits),
        datasets=lambda community: LimitedDatasets(community, limits),
    ),
)
```

The wrapper must preserve the corresponding public service contract. Category errors such as
`ConflictError` pass unchanged to the existing web and API boundary translation.

## Database setup and readiness

- `create_database_engine(database_url, **engine_options)` creates the configured SQLAlchemy
  engine and enables the required SQLite behavior.
- `create_session_factory(engine)` creates the Community session factory.
- `prepare_database(database_url, *, dataset_owner_mapping=None)` upgrades the database to the
  current schema.
- `database_is_ready(engine)` verifies connectivity and the applied migration revision.
- `expected_database_revision()` returns the single revision expected by this release.

```python
from refrncr.integration import (
    create_database_engine,
    database_is_ready,
    prepare_database,
)

prepare_database(database_url)
engine = create_database_engine(database_url)
if not database_is_ready(engine):
    raise RuntimeError("refrncr database is not ready")
```

## Administrator operations

- `CreateAdminConfiguration` contains the database URL, email address and display name for initial
  administrator creation.
- `create_initial_admin(configuration, password_reader=...)` migrates an installation and creates
  its first administrator.
- `recover_administrator_password(session_factory, email, new_password)` replaces an eligible
  administrator password through the existing service logic, without exposing a persistence
  adapter or creating its own engine.
- `CliError` is the secret-safe failure returned by these controlled operations.

```python
from refrncr.integration import CreateAdminConfiguration, create_initial_admin

create_initial_admin(
    CreateAdminConfiguration(database_url, "admin@example.com", "Administrator"),
    password_reader=read_password_without_logging,
)
```

For programmatic recovery, pass the session factory for the already selected installation or
tenant. `NotFoundError`, `ConflictError` and `ValidationError` are returned unchanged so the
integrator can translate the category without parsing messages.

```python
from refrncr.integration import (
    NotFoundError,
    ValidationError,
    recover_administrator_password,
)

try:
    recover_administrator_password(session_factory, email, new_password)
except NotFoundError:
    report_administrator_unavailable()
except ValidationError:
    report_password_policy_rejected()
```

Integrators must collect the password from a secret-safe input channel and must never log it. The
interactive Community CLI recovery command remains available, but is not part of this public
integration contract.

## Error categories

- `NotFoundError` represents an absent or deliberately concealed resource.
- `ConflictError` represents work that conflicts with current state.
- `ValidationError` represents invalid input.
- `AuthorizationError` represents denied access and maps to HTTP 403 at the browser boundary.

Catch the narrowest category needed at an application boundary. Do not depend on domain-specific
error implementations through internal modules.

```python
from refrncr.integration import AuthorizationError, ConflictError

try:
    perform_integrated_operation()
except AuthorizationError:
    return access_denied_response()
except ConflictError as error:
    return conflict_response(str(error))
```
