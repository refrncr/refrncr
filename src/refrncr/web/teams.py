"""Team-management browser blueprint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from flask import Blueprint, Flask, abort, current_app, redirect, render_template, request, url_for

from refrncr.services import (
    ConflictError,
    MembershipService,
    NotFoundError,
    TeamService,
    ValidationError,
)
from refrncr.web.routing import authenticated_route, capability_route

blueprint = Blueprint("teams", __name__)


class AuthenticationResolver(Protocol):
    def __call__(self) -> tuple[Any, Any]: ...


class CapabilityResolver(Protocol):
    def __call__(self, capability: str) -> tuple[Any, Any]: ...


class CsrfValidator(Protocol):
    def __call__(self, expected_token: str | None) -> None: ...


class NavigationResolver(Protocol):
    def __call__(self) -> str: ...


@dataclass(frozen=True)
class TeamsContext:
    teams: TeamService
    memberships: MembershipService
    require_authenticated: AuthenticationResolver
    require_capability: CapabilityResolver
    require_csrf: CsrfValidator
    active_navigation: NavigationResolver
    dataset_ui_enabled: bool


def _context() -> TeamsContext:
    return current_app.extensions["refrncr.teams"]


def _render_team_list(
    *,
    form_error=None,
    membership_error=None,
    form_target_id=None,
    form_name="",
    status=200,
):
    context = _context()
    actor, login_session = context.require_authenticated()
    if actor.role != "admin":
        return render_template(
            "pages/teams/readonly.html",
            teams=context.teams.list_teams(actor.id),
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=context.active_navigation(),
            dataset_ui_enabled=context.dataset_ui_enabled,
        ), status
    managed_teams = context.teams.list_teams(actor.id)
    team_users = {}
    for relationship in context.memberships.list_relationships(actor.id):
        team_users.setdefault(relationship.team_id, []).append(relationship)
    open_team = form_target_id or request.args.get("open_team")
    selected_team = next((team for team in managed_teams if team.id == open_team), None)
    selected_team_members = (
        context.memberships.list_team_members(actor.id, selected_team.id) if selected_team else []
    )
    selected_eligible_users = (
        context.memberships.list_eligible_users(actor.id, selected_team.id) if selected_team else []
    )
    return render_template(
        "pages/teams/list.html",
        teams=managed_teams,
        team_users=team_users,
        selected_team=selected_team,
        selected_team_members=selected_team_members,
        selected_eligible_users=selected_eligible_users,
        form_error=form_error,
        membership_error=membership_error,
        form_target_id=form_target_id,
        form_name=form_name,
        current_actor=actor,
        current_csrf=login_session.csrf_token,
        active_navigation=context.active_navigation(),
        dataset_ui_enabled=context.dataset_ui_enabled,
    ), status


@blueprint.get("/teams")
@authenticated_route
def team_list():
    return _render_team_list()


@blueprint.post("/teams")
@capability_route("can_manage_access")
def create_team():
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    try:
        team_name = request.form.get("team_label", request.form.get("name", ""))
        context.teams.create_team(actor.id, team_name)
    except (ValidationError, ConflictError) as error:
        return _render_team_list(
            form_error=str(error),
            form_name=request.form.get("team_label", request.form.get("name", "")),
            status=409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(url_for("teams.team_list"))


@blueprint.post("/teams/<team_id>/rename")
@capability_route("can_manage_access")
def rename_team(team_id: str):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    try:
        team_name = request.form.get("team_label", request.form.get("name", ""))
        context.teams.rename_team(actor.id, team_id, team_name)
    except NotFoundError:
        abort(404)
    except (ValidationError, ConflictError) as error:
        return _render_team_list(
            form_error=str(error),
            form_target_id=team_id,
            form_name=request.form.get("team_label", request.form.get("name", "")),
            status=409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(url_for("teams.team_list"))


@blueprint.post("/teams/<team_id>/delete")
@capability_route("can_manage_access")
def delete_team(team_id: str):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    try:
        context.teams.delete_team(actor.id, team_id)
    except NotFoundError:
        abort(404)
    return redirect(url_for("teams.team_list"))


@blueprint.get("/teams/<team_id>/members")
@capability_route("can_manage_access")
def team_members(team_id: str):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    managed_members = context.memberships.list_team_members(actor.id, team_id)
    eligible_users = context.memberships.list_eligible_users(actor.id, team_id)
    return render_template(
        "pages/teams/members.html",
        members=managed_members,
        eligible_users=eligible_users,
        team_id=team_id,
        current_actor=actor,
        current_csrf=login_session.csrf_token,
        active_navigation=context.active_navigation(),
        dataset_ui_enabled=context.dataset_ui_enabled,
    )


@blueprint.post("/teams/<team_id>/members")
@capability_route("can_manage_access")
def create_membership(team_id: str):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    try:
        context.memberships.create_membership(actor.id, request.form["user_id"], team_id)
    except NotFoundError:
        abort(404)
    except (ValidationError, ConflictError) as error:
        return _render_team_list(
            membership_error=str(error),
            form_target_id=team_id,
            status=409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(url_for("teams.team_list", open_team=team_id))


@blueprint.post("/teams/<team_id>/members/<membership_id>/delete")
@capability_route("can_manage_access")
def delete_membership(team_id: str, membership_id: str):
    context = _context()
    actor, login_session = context.require_capability("can_manage_access")
    context.require_csrf(login_session.csrf_token)
    try:
        context.memberships.delete_membership(actor.id, team_id, membership_id)
    except NotFoundError:
        abort(404)
    except (ValidationError, ConflictError) as error:
        return _render_team_list(
            membership_error=str(error),
            form_target_id=team_id,
            status=409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(url_for("teams.team_list", open_team=team_id))


def register_teams_blueprint(
    app: Flask,
    *,
    teams: TeamService,
    memberships: MembershipService,
    require_authenticated: AuthenticationResolver,
    require_capability: CapabilityResolver,
    require_csrf: CsrfValidator,
    active_navigation: NavigationResolver,
    dataset_ui_enabled: bool,
) -> None:
    app.extensions["refrncr.teams"] = TeamsContext(
        teams=teams,
        memberships=memberships,
        require_authenticated=require_authenticated,
        require_capability=require_capability,
        require_csrf=require_csrf,
        active_navigation=active_navigation,
        dataset_ui_enabled=dataset_ui_enabled,
    )
    app.register_blueprint(blueprint)
