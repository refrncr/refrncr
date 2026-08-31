from __future__ import annotations

import hmac
import secrets

from flask import Flask, abort, make_response, redirect, render_template_string, request, url_for

from refrncr.services import AuthorizationError, SessionService, UserManagementError, UserService

ANONYMOUS_CSRF_COOKIE = "__Host-refrncr_csrf"
SESSION_COOKIE = "refrncr_session"


def create_flask_app(users: UserService, sessions: SessionService) -> Flask:
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
            user = users.authenticate(request.form["email"], request.form["password"])
            if user is not None:
                existing_token = request.cookies.get(SESSION_COOKIE)
                if existing_token:
                    sessions.revoke(existing_token)
                token, _ = sessions.create(user)
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

    @app.errorhandler(UserManagementError)
    def handle_user_error(error: UserManagementError):
        status = 403 if isinstance(error, AuthorizationError) else 400
        return str(error), status

    return app
