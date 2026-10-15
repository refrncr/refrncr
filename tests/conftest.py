"""Shared pytest policy and collection fixtures."""

import os

import pytest
from support import _dataset_browser

POSTGRES_SKIP_REASON = "PostgreSQL tests require REFRNCR_TEST_POSTGRES_URL"


@pytest.fixture
def dataset_browser():
    return _dataset_browser()


def pytest_collection_modifyitems(config, items):
    """Mark PostgreSQL cases and skip them consistently when no database is configured."""
    del config
    postgres_configured = bool(os.environ.get("REFRNCR_TEST_POSTGRES_URL"))
    for item in items:
        parameters = getattr(item, "callspec", None)
        parameter_values = getattr(parameters, "params", {}).values() if parameters else ()
        is_postgres = (
            "postgres" in item.name.lower()
            or any("postgres" in fixture.lower() for fixture in item.fixturenames)
            or any(value == "postgresql" for value in parameter_values)
        )
        if not is_postgres:
            continue
        item.add_marker(pytest.mark.postgres)
        if not postgres_configured:
            item.add_marker(pytest.mark.skip(reason=POSTGRES_SKIP_REASON))
