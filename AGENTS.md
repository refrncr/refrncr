# refrncr development instructions

## Scope

These instructions apply to the entire `refrncr` repository.

`refrncr` is the Community/core application. Cloud-only functionality belongs in the separate `refrncr-cloud` repository.

## Source of truth

- Jira is the source of truth for product scope, user stories, features and priorities.
- Use the relevant `REF-...` issue as the implementation context when one exists.
- GitHub Issues are for community-facing bugs, feature suggestions and repository work, not as a replacement for Jira.
- Confluence contains architecture, repository strategy and development-process documentation.

## Git workflow

- Never implement work directly on `main`.
- Start new work from an up-to-date `main`.
- Use short-lived branches:
  - `feat/<short-description>`
  - `fix/<short-description>`
  - `chore/<short-description>`
  - `docs/<short-description>`
  - `test/<short-description>`
- Use Conventional Commits, for example `fix(web): preserve form feedback`.
- Keep commits and pull requests focused on one logical change.
- Do not merge a pull request until CI is green.
- Use squash merge into `main`.
- Do not force-push to `main`.
- Do not delete or rewrite unrelated work.

## Before starting work

Use:

```shell
git switch main
git pull
git status
git switch -c <type>/<short-description>
```

Do not start implementation if the working tree contains unexplained changes.

## Python environment

- Python version: 3.12 or newer as allowed by `pyproject.toml`.
- Use the repository virtual environment `.venv`.
- Prefer module invocation so commands use the active interpreter.

Install/update development dependencies with:

```shell
python -m pip install -e ".[dev]"
```

Prefer:

```shell
python -m pytest
python -m ruff check .
python -m pre_commit run --all-files
```

instead of relying on globally installed command shims.

## Code quality

- Follow the existing project structure and patterns before introducing new abstractions.
- Prefer simple, explicit code over premature frameworks or abstractions.
- Do not add a dependency unless it is justified by the current requirement.
- Keep public interfaces small and clear.
- Add or update tests for functional behaviour.
- Do not knowingly leave failing tests, lint errors or formatting errors.
- Do not weaken existing tests merely to make a change pass.


## Current tooling vs planned tooling

Only require tooling that is currently configured in the repository.

### Currently configured

The current development quality baseline consists of:

- pre-commit hooks;
- Ruff linting;
- Ruff formatting;
- pytest;
- SQLite database tests;
- PostgreSQL integration tests;
- GitHub Actions CI;
- Dependabot.

### Additional CI controls

The repository also runs migration and authorization-boundary tests, CodeQL, Gitleaks,
and release-time CycloneDX SBOM generation in GitHub Actions. These are CI controls, not
local replacements for the required commands above. Type checking, container scanning,
tenant-isolation testing, and automated release publication remain out of scope unless a
future story configures them.


## Required checks before completing a change

Run the checks currently configured for the repository:

```shell
python -m pre_commit run --all-files
python -m pytest
python -m ruff check .
```

All configured checks must pass before the work is considered complete.

GitHub Actions CI is the authoritative independent quality gate before merge.

If a check fails:

1. Investigate the failure.
2. Fix the underlying issue where appropriate.
3. Rerun the affected checks.
4. Do not report the task as complete while required checks are failing.

Do not claim that a quality control has passed if the corresponding tooling is not currently configured in the repository.

## Database

- Use SQLAlchemy 2.x for database access and ORM behaviour.
- Use Alembic for schema migrations.
- PostgreSQL is the production-style database.
- SQLite may be used for fast unit/in-memory tests where behaviour is database-independent.
- Database behaviour that could differ by engine should also be tested against PostgreSQL.
- Do not introduce schema changes without an Alembic migration once application tables exist.
- Do not hard-code production database credentials.
- Keep database URLs/configuration injectable.

## Tests

- Unit tests should be fast and isolated where possible.
- Test modules should verify behaviour through public interfaces, not read or parse source files. Guard, architecture, and supply-chain/license tests may statically analyse source code and repository files; UI and application behavior tests must assert rendered output or behavior, never source content.
- Test files should be named after the functionality they cover, not Jira ticket numbers.
- SQLite in-memory tests are appropriate for generic persistence behaviour.
- PostgreSQL integration tests are required for PostgreSQL-specific or persistence-critical behaviour.
- GitHub Actions provides PostgreSQL 16 as a service for the test job.
- Do not make CI tests depend on a developer's local Docker environment.

## Security

Use HTTP 403 for authenticated authorization failures. Resource-specific routes that deliberately
avoid disclosing whether an inaccessible object exists may return the documented uniform HTTP 404;
do not introduce new 404 masking without an explicit security contract and a regression test.

- Never commit passwords, API keys, access tokens, private keys or production credentials.
- Treat credentials used in local/CI test containers as disposable test-only values.
- Do not include sensitive vulnerability details in public GitHub Issues.
- Follow `SECURITY.md` for security reporting.
- Avoid logging secrets or sensitive data.
- Prefer least-privilege behaviour when adding authorization or access controls.
- After a form error, ordinary submitted values are returned escaped so users can correct them;
  passwords, API keys and session tokens are never rendered back into HTML.

## Community vs Cloud boundary

Keep Community/core functionality in this repository.

Do not add Cloud-only concerns here, including functionality that exists solely for:

- hosted-service operation;
- proprietary cloud orchestration;
- cloud billing or subscription management;
- cloud-only multi-tenant infrastructure.

Shared product capabilities that belong in the Community/core product should remain in this repository.

## Pull requests

Use the repository pull request template.

PR descriptions should explain:

- what changed;
- why it changed;
- how it was tested;
- security/database/API impact where relevant;
- the related Jira issue, or `N/A` for repository/governance work.

Do not claim checks passed unless they were actually run successfully.

## Documentation

Update documentation when behaviour, setup, architecture or developer workflow changes.

Do not duplicate authoritative product requirements from Jira into GitHub unless needed for implementation context.


## Jira implementation workflow

When implementing work associated with a Jira issue:

1. Read the complete Jira issue before changing code.
2. Identify the requested behaviour, acceptance criteria, dependencies and relevant constraints.
3. Inspect the existing codebase to understand the affected components and existing patterns.
4. Do not invent missing product requirements.
5. If a requirement is ambiguous and materially affects the implementation, stop and ask for clarification before implementing that part.
6. Create the implementation branch from an up-to-date `main`.
7. Use the Jira issue key when useful for traceability in the branch, commit or pull request.
8. Implement the smallest coherent change that satisfies the acceptance criteria.
9. Add or update automated tests for the behaviour.
10. Run all required local quality checks.
11. Review the resulting diff for unrelated or accidental changes.
12. Summarize what was implemented, any design decisions made, and the checks that were actually run.
13. Prepare the change for a pull request, but do not merge it unless explicitly instructed.


### Repository scope check

Before planning implementation, determine whether the Jira issue belongs in this repository.

For every Jira issue:

1. Identify whether the issue targets Community/core, Cloud, or both.
2. Check whether the requested functionality is compatible with the Community/core boundary defined above.
3. If the issue is Cloud-only, do not implement it in this repository.
4. If the issue explicitly requires Cloud-only infrastructure, hosted-service operation, proprietary orchestration, Cloud billing/subscriptions, or Cloud-only multi-tenancy, do not implement it here.
5. If the issue applies to both Community and Cloud, identify which part belongs in this repository before implementation.
6. If the repository scope cannot be determined confidently from the Jira issue and existing documentation, ask for clarification before modifying code.
7. Do not invent shared interfaces, abstractions or data models merely to accommodate a Cloud-only requirement.

A Jira issue being in the product backlog does not by itself mean that it belongs in the current repository.


### Planning before implementation

For non-trivial changes, create a short implementation plan before editing files.

The plan should identify:

- affected components/files;
- database or migration impact;
- API impact;
- security/authorization impact;
- tests that need to be added or changed;
- documentation impact.

Prefer extending existing patterns over introducing new architecture.

If the Jira issue conflicts with the current codebase, architecture documentation or another authoritative requirement, surface the conflict instead of silently choosing an interpretation.


## Iterative UI design

The product owner may refine the interface iteratively through visual feedback. Treat these requests as valid design exploration while preserving a coherent design system.

Before implementing each UI instruction, classify it as one of:

1. **Design token** — a reusable visual value such as colour, spacing, typography, radius, shadow, breakpoint or component dimension.
2. **Global component** — behaviour or presentation shared by all instances of a button, field, table, panel, alert or other component.
3. **Component variant** — a deliberate variation for a category such as data tables, destructive buttons or compact forms.
4. **Page-specific rule** — presentation unique to one page or workflow.
5. **Product functionality** — new behaviour, data, permissions, workflow or business rules.

Apply the change at the highest reusable level that accurately represents the request.

Rules:

- Do not add page-specific CSS when a token, global component or component variant is the correct abstraction.
- Do not generalise a genuinely page-specific decision merely to avoid local CSS.
- Reuse existing tokens, component markup, Jinja macros, includes and JavaScript controllers.
- Do not duplicate composite markup in Python strings or page templates.
- Keep routes focused on request handling and presentation data; do not move business logic into Jinja, CSS or JavaScript.
- Preserve server-side authorization, CSRF protection, IDOR protection, escaping, audit behaviour and transaction boundaries.
- Hiding an action is never a substitute for server-side authorization.
- Maintain semantic HTML, keyboard support, visible focus and WCAG 2.2 AA contrast.
- Make full content available to keyboard and assistive-technology users when visual truncation is used.
- Do not introduce a new framework, build process or dependency for a visual adjustment without approval.
- If a request changes functionality, architecture, security, persisted data or story scope, stop and ask for approval before implementing it.
- Before implementation, briefly state the selected classification and which shared token, component, variant or page rule will change.
- After approximately five related UI adjustments, perform a short consistency review for duplicated values, selectors, markup, states and accessibility behaviour.
- At the end of an iterative UI session, report:
  - changed tokens;
  - changed global components;
  - changed component variants;
  - deliberate page-specific rules;
  - functional changes;
  - remaining inconsistencies or follow-up work.


### Scope control

Implement the Jira issue that was requested.

Do not opportunistically:

- implement adjacent backlog items;
- perform unrelated refactoring;
- change public APIs unnecessarily;
- redesign existing architecture;
- add speculative functionality;
- fix unrelated issues discovered during implementation.

Small changes required to safely implement or test the requested issue are allowed, but they should be identified in the final summary.


## Traceability and architectural decisions

### End-to-end traceability

For product work, preserve this chain:

Product/design decision → Confluence → Jira REF issue → branch/PR → CI → main → release

When implementing a Jira story:

- Use the Jira issue key in the branch or pull request when practical.
- Link the Jira issue to the pull request.
- Keep the implementation aligned with the acceptance criteria in Jira.
- Do not duplicate the Jira backlog in GitHub Issues.
- Update relevant Confluence documentation when the implementation changes architecture, setup or development processes.

### Architecture decisions

For significant architectural decisions:

- Check existing architecture documentation and ADRs before introducing a new pattern.
- Prefer existing project patterns unless there is a clear reason to change them.
- Record significant new architectural decisions as an ADR.
- An ADR should describe:
  - context;
  - decision;
  - consequences;
  - alternatives considered;
  - status.
- Do not create an ADR for trivial implementation details.

### Impact review

Before considering a Jira story complete, explicitly consider whether it affects:

- authorization;
- tenant isolation;
- database schema;
- database migrations;
- API contracts;
- security;
- dependencies;
- documentation;
- architecture.

Only perform the checks that are relevant to the change, but do not skip a relevant impact area.

### CI and release authority

- Local checks provide fast developer feedback.
- GitHub Actions CI is the authoritative quality gate.
- Do not treat a change as fully complete until required CI checks pass.
- Releases must be produced through the established CI/release process.
- Do not create releases or release artifacts manually from a developer laptop unless the project process explicitly requires it.
- Do not create a permanent `develop` branch.
- `main` remains the primary integration branch.


## Definition of Done

A change is done when:

- the requested behaviour is implemented;
- relevant tests are added or updated;
- existing tests still pass;
- pre-commit passes;
- Ruff passes;
- no known secrets or unintended files were added;
- documentation is updated where required;
- the change is ready for a focused pull request;
- GitHub CI passes before merge.
