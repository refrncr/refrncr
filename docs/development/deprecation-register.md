# Deprecation register

## GrantService and `grants`

- **Status:** deprecated and isolated
- **Scope:** historical programmatic GrantService compatibility only
- **Current behaviour:** browser routes and current Community APIs authorize
  through named capabilities on `AuthorizationService`.
- **Persistence:** the `grants` table and its data remain unchanged. No
  migration removes or transforms them.
- **Removal:** requires a separate product and architecture decision.
