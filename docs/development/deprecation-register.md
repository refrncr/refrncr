# Deprecation register

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
