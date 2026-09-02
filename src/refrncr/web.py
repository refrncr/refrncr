from __future__ import annotations

import hmac
import secrets

from flask import Flask, abort, g, make_response, redirect, render_template_string, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from refrncr.browser_security import (
    HTTPS_ANONYMOUS_CSRF_COOKIE,
    HTTPS_SESSION_COOKIE,
    BrowserCookiePolicy,
)
from refrncr.login_security import (
    MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS,
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
    canonical_account_key,
)
from refrncr.services import (
    AuthenticationService,
    AuthorizationError,
    MembershipService,
    SessionService,
    TeamService,
    UserManagementError,
    UserService,
)

ANONYMOUS_CSRF_COOKIE = HTTPS_ANONYMOUS_CSRF_COOKIE
SESSION_COOKIE = HTTPS_SESSION_COOKIE
GENERIC_LOGIN_FAILURE = "Invalid credentials"
MAX_BROWSER_FORM_CONTENT_LENGTH = 16 * 1024


def create_flask_app(
    users: UserService,
    authentication: AuthenticationService,
    sessions: SessionService,
    teams: TeamService,
    memberships: MembershipService,
    *,
    allow_loopback_http: bool = False,
    login_limiter: LoginAttemptLimiter | None = None,
    source_resolver: SourceIdentityResolver | None = None,
    authentication_admission: BrowserAuthenticationAdmissionGuard | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BROWSER_FORM_CONTENT_LENGTH
    login_limiter = login_limiter or LoginAttemptLimiter()
    source_resolver = source_resolver or SourceIdentityResolver()
    authentication_admission = authentication_admission or BrowserAuthenticationAdmissionGuard()
    cookie_policy = BrowserCookiePolicy(
        session_max_age=int(sessions.lifetime.total_seconds()),
        allow_loopback_http=allow_loopback_http,
    )

    def cookie_settings():
        return cookie_policy.for_request(
            scheme=request.scheme,
            host=request.host,
            remote_address=request.remote_addr,
        )

    def require_cookie_settings():
        settings = cookie_settings()
        if settings is None:
            abort(403, description="Authenticated browser access requires HTTPS")
        return settings

    @app.after_request
    def clear_stale_session_cookie(response):
        settings = getattr(g, "stale_cookie_settings", None)
        if settings is not None:
            response.delete_cookie(settings.session_name, **settings.common_options)
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def handle_oversized_request(_error):
        if request.path == "/login":
            return GENERIC_LOGIN_FAILURE, 401
        return "Request is too large", 413

    def authenticated():
        settings = cookie_settings()
        if settings is None:
            return None
        token = request.cookies.get(settings.session_name)
        if not token:
            return None
        resolved = sessions.resolve_with_session(token)
        if resolved is None:
            g.stale_cookie_settings = settings
        return resolved

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
        settings = require_cookie_settings()
        token = secrets.token_urlsafe(32)
        response = make_response(render_template_string(template, csrf_token=token))
        response.set_cookie(
            settings.anonymous_csrf_name,
            token,
            **settings.common_options,
        )
        return response

    @app.get("/")
    def index():
        return render_template_string("<h1>refrncr</h1><a href='/login'>Log in</a>")

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        settings = require_cookie_settings()
        if users.has_users():
            return redirect(url_for("login"))
        if request.method == "POST":
            require_csrf(request.cookies.get(settings.anonymous_csrf_name))
            users.create_initial_admin(
                request.form["email"],
                request.form["display_name"],
                request.form["password"],
            )
            response = redirect(url_for("login"))
            response.delete_cookie(
                settings.anonymous_csrf_name,
                **settings.common_options,
            )
            return response
        return form_with_anonymous_csrf(
            "<form method='post'><input name='email'><input name='display_name'>"
            "<input name='password' type='password'><input type='hidden' name='csrf_token' "
            "value='{{ csrf_token }}'><button>Create administrator</button></form>"
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        settings = require_cookie_settings()
        if request.method == "POST":
            require_csrf(request.cookies.get(settings.anonymous_csrf_name))
            submitted_email = request.form["email"]
            oversized_identifier = len(submitted_email) > MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS
            account_key = canonical_account_key(submitted_email)
            source_key = source_resolver.resolve(
                request.remote_addr,
                request.headers.get("X-Forwarded-For"),
            )
            attempt = login_limiter.reserve(source_key, account_key)
            if attempt is None:
                return GENERIC_LOGIN_FAILURE, 401
            if oversized_identifier:
                attempt.failed()
                return GENERIC_LOGIN_FAILURE, 401
            permit = authentication_admission.try_acquire()
            if permit is None:
                attempt.cancel()
                return GENERIC_LOGIN_FAILURE, 401
            try:
                with permit:
                    principal = authentication.authenticate(
                        submitted_email, request.form["password"]
                    )
            except Exception:
                attempt.failed()
                raise
            if principal is not None:
                attempt.succeeded()
                existing_token = request.cookies.get(settings.session_name)
                if existing_token:
                    sessions.revoke(existing_token)
                token, _ = sessions.create(principal)
                response = redirect(url_for("index"))
                response.set_cookie(
                    settings.session_name,
                    token,
                    max_age=settings.session_max_age,
                    **settings.common_options,
                )
                response.delete_cookie(settings.anonymous_csrf_name, **settings.common_options)
                return response
            attempt.failed()
            return GENERIC_LOGIN_FAILURE, 401
        return form_with_anonymous_csrf(
            "<form method='post'><input name='email'><input name='password' type='password'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf_token }}'>"
            "<button>Log in</button></form>"
        )

    @app.post("/logout")
    def logout():
        settings = cookie_settings()
        token = request.cookies.get(settings.session_name) if settings else None
        if not token:
            return redirect(url_for("index"))
        resolved = sessions.resolve_with_session(token)
        if resolved is None:
            return redirect(url_for("index"))
        require_csrf(resolved[1].csrf_token)
        sessions.revoke(token)
        response = redirect(url_for("index"))
        response.delete_cookie(settings.session_name, **settings.common_options)
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
