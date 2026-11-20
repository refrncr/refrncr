# Type checking

The repository runs mypy in non-strict mode for the domain packages introduced by ADR-026,
the Community persistence adapters, the audit and presentation packages, and the public
`refrncr.integration` facade. Run the same check locally and in CI with:

```shell
python tools/typecheck.py
```

The checked paths and configuration live in `pyproject.toml`; the helper deliberately accepts
no separate path list so pre-commit and CI cannot drift apart. Type checking all of `src/` and
enabling strict mode are follow-up work. Imports outside the selected paths are skipped so a clean
environment cannot silently broaden this story to `web/`, `api/`, `app.py` or `cli.py`; modules
inside the selected paths remain explicit mypy inputs and are all checked.

The package-specific disabled error codes are the initial non-strict baseline for existing
annotations around Dataset services, import workflows and SQLAlchemy adapters. Clean packages
use mypy's default checks without overrides. New suppressions must remain narrowly scoped and
documented in `pyproject.toml`; prefer correcting an annotation when that stays within the
current story and does not change runtime behavior.
