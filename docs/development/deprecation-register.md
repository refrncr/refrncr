# Deprecation register

## `refrncr.services`

- **Status:** deprecated compatibility facade
- **Scope:** historical imports of services, contracts and errors that now live in their
  owning domain packages.
- **Current behaviour:** importing the module emits a `DeprecationWarning` that points
  integrators to the supported `refrncr.integration` contract.
- **Removal:** REF-353, after Cloud confirms that production and integration tests no
  longer import this facade.

## Cloud compatibility import paths

- **Status:** deprecated compatibility facades
- **Scope:** `refrncr.db.session` and `refrncr.models`
- **Current behaviour:** the modules directly re-export the database factories and ORM
  models still imported by Cloud production code and integration tests. Importing either
  module emits a `DeprecationWarning` that points integrators to `refrncr.integration`.
- **Removal:** REF-353, after Cloud confirms that its own integration tests no longer use
  the deprecated paths.

## GrantService and `grants`

- **Status:** deprecated and isolated
- **Scope:** historical programmatic GrantService compatibility only
- **Current behaviour:** browser routes and current Community APIs authorize
  through named capabilities on `AuthorizationService`.
- **Persistence:** the `grants` table and its data remain unchanged. No
  migration removes or transforms them.
- **Removal:** requires a separate product and architecture decision.
