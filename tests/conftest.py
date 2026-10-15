"""Shared pytest policy and collection fixtures."""

import os

import pytest
from support import _dataset_browser

POSTGRES_SKIP_REASON = "PostgreSQL tests require REFRNCR_TEST_POSTGRES_URL"


@pytest.fixture
def dataset_browser():
    return _dataset_browser()


def pytest_collection_modifyitems(config, items):
    """Skip explicitly marked PostgreSQL cases when no database is configured."""
    del config
    postgres_configured = bool(os.environ.get("REFRNCR_TEST_POSTGRES_URL"))
    for item in items:
        if item.get_closest_marker("postgres") is None:
            continue
        if not postgres_configured:
            item.add_marker(pytest.mark.skip(reason=POSTGRES_SKIP_REASON))
