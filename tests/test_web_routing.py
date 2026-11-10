import pytest

from refrncr.web.routing import (
    anonymous_csrf_route,
    authenticated_route,
    bounded_import_route,
    capability_route,
    concept_route,
    dataset_not_found_route,
    no_store_route,
    public_route,
    upload_limit_route,
)


@pytest.mark.parametrize(
    ("decorator", "attribute", "value"),
    (
        (public_route, "public_route", True),
        (anonymous_csrf_route, "anonymous_csrf_route", True),
        (concept_route, "concept_route", True),
        (authenticated_route, "required_capability", "can_access_community"),
        (no_store_route, "no_store_route", True),
        (upload_limit_route, "upload_limit_route", True),
        (bounded_import_route, "bounded_import_route", True),
        (dataset_not_found_route, "dataset_not_found_route", True),
    ),
)
def test_route_decorators_preserve_the_view_and_attach_metadata(decorator, attribute, value):
    def view():
        return None

    decorated = decorator(view)

    assert decorated is view
    assert getattr(decorated, attribute) == value


def test_capability_route_records_the_declared_capability():
    def view():
        return None

    decorated = capability_route("can_manage_access")(view)

    assert decorated is view
    assert decorated.required_capability == "can_manage_access"
