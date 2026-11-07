"""Route metadata used by the shared browser request hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def public_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a browser route that may be reached without an active session."""
    view.public_route = True
    return view


def anonymous_csrf_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a route whose state changes use the anonymous CSRF cookie."""
    view.anonymous_csrf_route = True
    return view


def concept_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a route that needs concept context and no-store response headers."""
    view.concept_route = True
    return view


def capability_route[View: Callable[..., Any]](capability: str) -> Callable[[View], View]:
    """Declare the capability required by a browser route."""

    def decorate(view: View) -> View:
        view.required_capability = capability
        return view

    return decorate


def authenticated_route[View: Callable[..., Any]](view: View) -> View:
    """Declare the capability shared by every authenticated browser route."""
    return capability_route("can_access_community")(view)


def no_store_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a response as containing sensitive non-cacheable content."""
    view.no_store_route = True
    return view


def upload_limit_route[View: Callable[..., Any]](view: View) -> View:
    """Apply the bounded upload request policy to a route."""
    view.upload_limit_route = True
    return view


def bounded_import_route[View: Callable[..., Any]](view: View) -> View:
    """Apply the bounded import-form request policy to a route."""
    view.bounded_import_route = True
    return view


def dataset_not_found_route[View: Callable[..., Any]](view: View) -> View:
    """Use the uniform dataset-not-found response contract for a route."""
    view.dataset_not_found_route = True
    return view
