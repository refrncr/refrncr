"""Hierarchy blueprint and typed application context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Flask, current_app
from werkzeug.local import LocalProxy

from refrncr.dataset_versions import DatasetVersionService
from refrncr.hierarchy import HierarchyService
from refrncr.services import AuthorizationService
from refrncr.web.routing import DatasetBlueprint

blueprint = DatasetBlueprint("hierarchies", __name__)


@dataclass(frozen=True)
class HierarchiesContext:
    authorization: AuthorizationService
    dataset_versions: DatasetVersionService
    hierarchy: HierarchyService
    require_authenticated: Callable[[], Any]
    render_authenticated_template: Callable[..., Any]
    exact_expected_revision: Callable[[], int]


def _context() -> HierarchiesContext:
    return current_app.extensions["refrncr.hierarchies"]


authorization = LocalProxy(lambda: _context().authorization)
dataset_versions = LocalProxy(lambda: _context().dataset_versions)
hierarchy = LocalProxy(lambda: _context().hierarchy)


def require_authenticated():
    return _context().require_authenticated()


def render_authenticated_template(template_name: str, **context):
    return _context().render_authenticated_template(template_name, **context)


def exact_expected_revision() -> int:
    return _context().exact_expected_revision()


from refrncr.web.hierarchies import maintenance as _maintenance  # noqa: E402, F401
from refrncr.web.hierarchies import views as _views  # noqa: E402, F401


def register_hierarchies_blueprint(
    app: Flask,
    *,
    authorization: AuthorizationService | None,
    dataset_versions: DatasetVersionService | None,
    hierarchy: HierarchyService | None,
    require_authenticated: Callable[[], Any],
    render_authenticated_template: Callable[..., Any],
    exact_expected_revision: Callable[[], int],
) -> None:
    if authorization is not None and dataset_versions is not None and hierarchy is not None:
        app.extensions["refrncr.hierarchies"] = HierarchiesContext(
            authorization=authorization,
            dataset_versions=dataset_versions,
            hierarchy=hierarchy,
            require_authenticated=require_authenticated,
            render_authenticated_template=render_authenticated_template,
            exact_expected_revision=exact_expected_revision,
        )
    app.register_blueprint(blueprint)
