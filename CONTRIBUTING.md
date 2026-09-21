# Contributing

Use Python 3.12 or newer and the repository `.venv`. Install development tools with
`python -m pip install -e ".[dev]"`, work on a short-lived branch, and run Ruff, pre-commit and the
full pytest suite before proposing a pull request.

Runtime dependency changes must preserve compatible public ranges in `pyproject.toml`, update the
matching `requirements/release.in`, regenerate the hashed `requirements/release.txt`, and include
the audit/review evidence. Dependabot updates follow the same review. All external GitHub Actions
must be pinned to a full commit SHA. Scanner findings must be fixed or handled through the bounded,
approved exception process; never add an undocumented suppression. See
[Release dependency and supply-chain operations](docs/development/supply-chain.md).
