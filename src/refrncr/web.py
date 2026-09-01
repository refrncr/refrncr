from __future__ import annotations

import hmac
import secrets

from flask import Flask, abort, make_response, redirect, render_template_string, request, url_for

from refrncr.services import (
    AuthenticationService,
    AuthorizationError,
    MembershipService,
    SessionService,
    TeamService,
    UserManagementError,
    UserService,
)

ANONYMOUS_CSRF_COOKIE = "__Host-refrncr_csrf"
SESSION_COOKIE = "refrncr_session"


def create_flask_app(
    users: UserService,
    authentication: AuthenticationService,
    sessions: SessionService,
    teams: TeamService,
    memberships: MembershipService,
) -> Flask:
    app = Flask(__name__)

    def authenticated():
        token = request.cookies.get(SESSION_COOKIE)
        return sessions.resolve_with_session(token) if token else None

    def require_admin():
        resolved = authenticated()
        if resolved is None or resolved[0].role != "admin":
            abort(403)
        return resolved

    def require_csrf(expected_token: str | None) -> None:
        submitted_token = request.form.get("csrf_token")
        try:
            matches = (
                expected_token is not None
                and submitted_token is not None
                and hmac.compare_digest(expected_token, submitted_token)
            )
        except TypeError:
            matches = False
        if not matches:
            abort(403)

    def form_with_anonymous_csrf(template: str):
        token = secrets.token_urlsafe(32)
        response = make_response(render_template_string(template, csrf_token=token))
        response.set_cookie(
            ANONYMOUS_CSRF_COOKIE,
            token,
            secure=True,
            httponly=True,
            samesite="Lax",
        )
        return response

    @app.get("/")
    def index():
        return render_template_string("<h1>refrncr</h1><a href='/login'>Log in</a>")

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if request.method == "POST":
            require_csrf(request.cookies.get(ANONYMOUS_CSRF_COOKIE))
            users.create_initial_admin(
                request.form["email"],
                request.form["display_name"],
                request.form["password"],
            )
            response = redirect(url_for("login"))
            response.delete_cookie(
                ANONYMOUS_CSRF_COOKIE,
                secure=True,
                httponly=True,
                samesite="Lax",
            )
            return response
        return form_with_anonymous_csrf(
            "<form method='post'><input name='email'><input name='display_name'>"
            "<input name='password' type='password'><input type='hidden' name='csrf_token' "
            "value='{{ csrf_token }}'><button>Create administrator</button></form>"
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            require_csrf(request.cookies.get(ANONYMOUS_CSRF_COOKIE))
            principal = authentication.authenticate(request.form["email"], request.form["password"])
            if principal is not None:
                existing_token = request.cookies.get(SESSION_COOKIE)
                if existing_token:
                    sessions.revoke(existing_token)
                token, _ = sessions.create(principal)
                response = redirect(url_for("index"))
                response.set_cookie(
                    SESSION_COOKIE,
                    token,
                    secure=True,
                    httponly=True,
                    samesite="Lax",
                )
                response.delete_cookie(
                    ANONYMOUS_CSRF_COOKIE, secure=True, httponly=True, samesite="Lax"
                )
                return response
            return "Invalid credentials", 401
        return form_with_anonymous_csrf(
            "<form method='post'><input name='email'><input name='password' type='password'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf_token }}'>"
            "<button>Log in</button></form>"
        )

    @app.post("/logout")
    def logout():
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            resolved = sessions.resolve_with_session(token)
            if resolved is not None:
                require_csrf(resolved[1].csrf_token)
            sessions.revoke(token)
        response = redirect(url_for("index"))
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="Lax")
        return response

    @app.get("/users")
    def user_list():
        actor, login_session = require_admin()
        managed_users = users.list_users(actor.id)
        return render_template_string(
            "<h1>Users</h1><ul>{% for user in users %}<li>"
            "{{ user.display_name }} — {{ user.email }} ({{ user.role }}) "
            "active={{ user.is_active }} deleted={{ user.is_deleted }}"
            "<form method='post' action='/users/{{ user.id }}/email'>"
            "<input name='email'><input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Change email</button></form>"
            "<form method='post' action='/users/{{ user.id }}/disable'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Disable</button></form>"
            "<form method='post' action='/users/{{ user.id }}/enable'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Enable</button></form>"
            "<form method='post' action='/users/{{ user.id }}/delete'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Delete</button></form></li>{% endfor %}</ul>"
            "<form method='post' action='/users'><input name='email'><input name='display_name'>"
            "<input name='password' type='password'><input type='hidden' name='csrf_token' "
            "value='{{ csrf }}'><button>Create user</button></form>",
            users=managed_users,
            csrf=login_session.csrf_token,
        )

    @app.post("/users")
    def create_user():
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        users.create_user(
            actor.id,
            request.form["email"],
            request.form["display_name"],
            request.form["password"],
            request.form.get("role", "user"),
        )
        return redirect(url_for("user_list"))

    @app.post("/users/<target_id>/<action>")
    def change_user_state(target_id: str, action: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        operations = {
            "disable": users.disable_user,
            "enable": users.enable_user,
            "delete": users.delete_user,
        }
        operation = operations.get(action)
        if operation is None:
            abort(404)
        operation(actor.id, target_id)
        return redirect(url_for("user_list"))

    @app.post("/users/<target_id>/email")
    def change_user_email(target_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        users.change_email(actor.id, target_id, request.form["email"])
        return redirect(url_for("user_list"))

    @app.get("/teams")
    def team_list():
        actor, login_session = require_admin()
        managed_teams = teams.list_teams(actor.id)
        return render_template_string(
            "<h1>Teams</h1><ul>{% for team in teams %}<li>"
            "{{ team.name }} <a href='/teams/{{ team.id }}/members'>Manage members</a>"
            "<form method='post' action='/teams/{{ team.id }}/rename'>"
            "<input name='name'><input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Rename team</button></form>"
            "<form method='post' action='/teams/{{ team.id }}/delete'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Delete team</button></form></li>{% endfor %}</ul>"
            "<form method='post' action='/teams'><input name='name'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Create team</button></form>",
            teams=managed_teams,
            csrf=login_session.csrf_token,
        )

    @app.post("/teams")
    def create_team():
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        teams.create_team(actor.id, request.form["name"])
        return redirect(url_for("team_list"))

    @app.post("/teams/<team_id>/rename")
    def rename_team(team_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        teams.rename_team(actor.id, team_id, request.form["name"])
        return redirect(url_for("team_list"))

    @app.post("/teams/<team_id>/delete")
    def delete_team(team_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        teams.delete_team(actor.id, team_id)
        return redirect(url_for("team_list"))

    @app.get("/teams/<team_id>/members")
    def team_members(team_id: str):
        actor, login_session = require_admin()
        managed_members = memberships.list_team_members(actor.id, team_id)
        eligible_users = memberships.list_eligible_users(actor.id, team_id)
        return render_template_string(
            "<h1>Team members</h1>"
            "{% if members %}<ul>{% for membership, user in members %}<li>"
            "{{ user.display_name }} — {{ user.email }}"
            "<form method='post' action='/teams/{{ team_id }}/members/"
            "{{ membership.id }}/delete'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Remove member</button></form></li>{% endfor %}</ul>"
            "{% else %}<p>This team has no members.</p>{% endif %}"
            "<form method='post' action='/teams/{{ team_id }}/members'>"
            "<select name='user_id'>{% for user in eligible_users %}"
            "<option value='{{ user.id }}'>{{ user.display_name }} — {{ user.email }}</option>"
            "{% endfor %}</select><input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Add member</button></form>",
            members=managed_members,
            eligible_users=eligible_users,
            team_id=team_id,
            csrf=login_session.csrf_token,
        )

    @app.post("/teams/<team_id>/members")
    def create_membership(team_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        memberships.create_membership(actor.id, request.form["user_id"], team_id)
        return redirect(url_for("team_members", team_id=team_id))

    @app.post("/teams/<team_id>/members/<membership_id>/delete")
    def delete_membership(team_id: str, membership_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        memberships.delete_membership(actor.id, team_id, membership_id)
        return redirect(url_for("team_members", team_id=team_id))

    @app.errorhandler(UserManagementError)
    def handle_user_error(error: UserManagementError):
        status = 403 if isinstance(error, AuthorizationError) else 400
        return str(error), status

    return app
