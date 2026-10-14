# Contributing

The repository is licensed under Apache-2.0; see the root [LICENSE](LICENSE). Development setup,
the SQLite/PostgreSQL test matrix, and required checks are maintained in the [README Development
section](README.md#development).

Use Python 3.12 or newer and the repository `.venv`. Install development tools with
`python -m pip install -e ".[dev]"`, work on a short-lived branch, and run Ruff, pre-commit and the
full pytest suite before proposing a pull request.

For Jira product work, title the pull request as `REF-<number>: <imperative summary>`, matching
the established project history (for example, `REF-176: Add effective-date and version selection
to the REST API`). Use an imperative summary without a Jira prefix for repository work that has no
related Jira issue. Commits continue to follow Conventional Commits.

Runtime dependency changes must preserve compatible public ranges in `pyproject.toml`, update the
matching `requirements/release.in`, regenerate the hashed `requirements/release.txt`, and include
the audit/review evidence. Dependabot updates follow the same review. All external GitHub Actions
must be pinned to a full commit SHA. Scanner findings must be fixed or handled through the bounded,
approved exception process; never add an undocumented suppression. See
[Release dependency and supply-chain operations](docs/development/supply-chain.md).
