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
- Use Conventional Commits.
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

### Planned quality controls

The following are planned but are not yet mandatory repository checks:

- type checking;
- migration tests;
- dedicated SAST;
- dedicated secret scanning;
- container scanning;
- SBOM generation;
- end-to-end tests;
- authorization-matrix tests;
- tenant-isolation tests;
- automated release pipelines.

Do not claim that planned tooling is configured or required.

When a Jira story requires one of these controls and the tooling does not yet exist, identify the gap rather than silently assuming the control is available.


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
- SQLite in-memory tests are appropriate for generic persistence behaviour.
- PostgreSQL integration tests are required for PostgreSQL-specific or persistence-critical behaviour.
- GitHub Actions provides PostgreSQL 16 as a service for the test job.
- Do not make CI tests depend on a developer's local Docker environment.

## Security

- Never commit passwords, API keys, access tokens, private keys or production credentials.
- Treat credentials used in local/CI test containers as disposable test-only values.
- Do not include sensitive vulnerability details in public GitHub Issues.
- Follow `SECURITY.md` for security reporting.
- Avoid logging secrets or sensitive data.
- Prefer least-privilege behaviour when adding authorization or access controls.

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
