"""User-management browser blueprint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from flask import Blueprint, Flask, abort, current_app, redirect, render_template, request, url_for

from refrncr.services import (
    ConflictError,
    MembershipService,
    NotFoundError,
    UserService,
    ValidationError,
)
from refrncr.web.routing import capability_route

blueprint = Blueprint("users", __name__)


class CapabilityResolver(Protocol):
    def __call__(self, capability: str) -> tuple[Any, Any]: ...


class CsrfValidator(Protocol):
    def __call__(self, expected_token: str | None) -> None: ...


class NavigationResolver(Protocol):
    def __call__(self) -> str: ...


@dataclass(frozen=True)
class UsersContext:
    users: UserService
    memberships: MembershipService
    require_capability: CapabilityResolver
    require_csrf: CsrfValidator
    active_navigation: NavigationResolver
    dataset_ui_enabled: bool


def _context() -> UsersContext:
    return current_app.extensions["refrncr.users"]


def _render_user_list(
    *,
    form_error: str | None = None,
    form_target_id: str | None = None,
    form_data: Any = None,
    status: int = 200,
):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    managed_users = context.users.list_users(actor.id)
    user_teams = {}
    for relationship in context.memberships.list_relationships(actor.id):
        user_teams.setdefault(relationship.user_id, []).append(relationship)
    form_data = form_data or {}
    return render_template(
        "pages/users/list.html",
        users=managed_users,
        user_teams=user_teams,
        open_user=form_target_id or request.args.get("open_user"),
        form_error=form_error,
        form_target_id=form_target_id,
        form_email=form_data.get("invitee_email", form_data.get("email", "")),
        form_display_name=form_data.get("invitee_display_name", form_data.get("display_name", "")),
        form_role=form_data.get("role", "user"),
        current_actor=actor,
        current_csrf=login_session.csrf_token,
        active_navigation=context.active_navigation(),
        dataset_ui_enabled=context.dataset_ui_enabled,
    ), status


@blueprint.get("/users")
@capability_route("can_manage_access")
def user_list():
    return _render_user_list()


@blueprint.post("/users")
@capability_route("can_manage_access")
def create_user():
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    try:
        context.users.create_user(
            actor.id,
            request.form.get("invitee_email", request.form.get("email", "")),
            request.form.get("invitee_display_name", request.form.get("display_name", "")),
            request.form.get("invitee_password", request.form.get("password", "")),
            request.form.get("role", "user"),
        )
    except (ValidationError, ConflictError) as error:
        return _render_user_list(
            form_error=str(error),
            form_data=request.form,
            status=409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(url_for("users.user_list"))


@blueprint.post("/users/<target_id>/<action>")
@capability_route("can_manage_access")
def change_user_state(target_id: str, action: str):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    operations = {
        "disable": context.users.disable_user,
        "enable": context.users.enable_user,
        "delete": context.users.delete_user,
    }
    operation = operations.get(action)
    if operation is None:
        abort(404)
    try:
        operation(actor.id, target_id)
    except NotFoundError:
        abort(404)
    return redirect(url_for("users.user_list"))


@blueprint.post("/users/<target_id>/email")
@capability_route("can_manage_access")
def change_user_email(target_id: str):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    try:
        context.users.change_email(actor.id, target_id, request.form.get("email", ""))
    except NotFoundError:
        abort(404)
    except (ValidationError, ConflictError) as error:
        return _render_user_list(
            form_error=str(error),
            form_target_id=target_id,
            form_data=request.form,
            status=409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(url_for("users.user_list"))


def register_users_blueprint(
    app: Flask,
    *,
    users: UserService,
    memberships: MembershipService,
    require_capability: CapabilityResolver,
    require_csrf: CsrfValidator,
    active_navigation: NavigationResolver,
    dataset_ui_enabled: bool,
) -> None:
    app.extensions["refrncr.users"] = UsersContext(
        users=users,
        memberships=memberships,
        require_capability=require_capability,
        require_csrf=require_csrf,
        active_navigation=active_navigation,
        dataset_ui_enabled=dataset_ui_enabled,
    )
    app.register_blueprint(blueprint)
