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

Existing errors in Dataset services, import workflows and SQLAlchemy adapters are suppressed only
on the affected line with the specific mypy error code. `warn_unused_ignores` rejects suppressions
that are no longer needed. `tests/test_typechecking.py` rejects unqualified `type: ignore`
comments and prevents the number of scoped suppressions from growing. Prefer correcting an
annotation when that stays within the current story and does not change runtime behavior; lower
the guard's maximum when an existing suppression is removed.
