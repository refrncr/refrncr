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
    DATASET_FIELD_TYPES,
    AuthenticationService,
    AuthorizationError,
    AuthorizationService,
    ConflictError,
    DatasetBusinessKeyService,
    DatasetFieldService,
    DatasetService,
    MembershipService,
    NotFoundError,
    SessionService,
    TeamService,
    UserManagementError,
    UserService,
    ValidationError,
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
    authorization: AuthorizationService | None = None,
    datasets: DatasetService | None = None,
    dataset_fields: DatasetFieldService | None = None,
    dataset_business_keys: DatasetBusinessKeyService | None = None,
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
    dataset_dependencies = (authorization, datasets, dataset_fields, dataset_business_keys)
    if any(dependency is not None for dependency in dataset_dependencies) and not all(
        dependency is not None for dependency in dataset_dependencies
    ):
        raise ValueError("Dataset browser dependencies must be supplied together")
    dataset_ui_enabled = all(dependency is not None for dependency in dataset_dependencies)
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

    def require_authenticated():
        resolved = authenticated()
        if resolved is None:
            abort(403)
        return resolved

    def require_dataset_dependencies():
        if (
            authorization is None
            or datasets is None
            or dataset_fields is None
            or dataset_business_keys is None
        ):
            abort(404)
        return authorization, datasets, dataset_fields, dataset_business_keys

    def dataset_route(rule: str, **options):
        if dataset_ui_enabled:
            return app.route(rule, **options)
        return lambda function: function

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
        resolved = authenticated()
        return render_template_string(
            "<h1>refrncr</h1>"
            "{% if resolved %}{% if dataset_ui_enabled %}<nav>"
            "<a href='{{ url_for(\"dataset_list\") }}'>Datasets</a>"
            "</nav>{% endif %}{% else %}<a href='{{ url_for(\"login\") }}'>"
            "Log in</a>{% endif %}",
            resolved=resolved,
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets", methods=["GET"])
    def dataset_list():
        actor, _login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service = (
            require_dataset_dependencies()
        )
        visible_datasets = dataset_service.list_datasets(actor.id)
        return render_template_string(
            "<nav><a href='{{ url_for(\"index\") }}'>Home</a></nav>"
            "<h1>Datasets</h1>"
            "{% if actor.role == 'admin' %}<a href='{{ url_for(\"new_dataset\") }}'>"
            "Create dataset</a>{% endif %}"
            "{% if datasets %}<ul>{% for dataset in datasets %}<li>"
            "<a href='{{ url_for(\"dataset_detail\", key=dataset.key) }}'>"
            "{{ dataset.name }}</a> <code>{{ dataset.key }}</code>"
            "{% if dataset.description %}<p>{{ dataset.description }}</p>{% endif %}"
            "</li>{% endfor %}</ul>{% else %}<p>No datasets are available.</p>{% endif %}",
            actor=actor,
            datasets=visible_datasets,
        )

    def render_dataset_create_form(
        *, key: str = "", name: str = "", description: str = "", error=None
    ):
        _actor, login_session = require_admin()
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_list\") }}'>Datasets</a></nav>"
            "<h1>Create dataset</h1>{% if error %}<p role='alert'>{{ error }}</p>{% endif %}"
            "<form method='post' action='{{ url_for(\"create_dataset\") }}'>"
            "<label>Key <input name='key' value='{{ key }}'></label>"
            "<label>Name <input name='name' value='{{ name }}'></label>"
            "<label>Description <textarea name='description'>{{ description }}</textarea></label>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Create dataset</button></form>",
            key=key,
            name=name,
            description=description,
            error=error,
            csrf=login_session.csrf_token,
        )

    @dataset_route("/datasets/new", methods=["GET"])
    def new_dataset():
        require_dataset_dependencies()
        return render_dataset_create_form()

    @dataset_route("/datasets", methods=["POST"])
    def create_dataset():
        actor, login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        key = request.form.get("key", "")
        name = request.form.get("name", "")
        description = request.form.get("description", "")
        try:
            dataset = dataset_service.create_dataset(actor.id, key, name, description)
        except (ValidationError, ConflictError) as error:
            response = render_dataset_create_form(
                key=key,
                name=name,
                description=description,
                error=str(error),
            )
            return response, 409 if isinstance(error, ConflictError) else 400
        return redirect(url_for("dataset_detail", key=dataset.key))

    @dataset_route("/datasets/<key>", methods=["GET"])
    def dataset_detail(key: str):
        actor, _login_session = require_authenticated()
        authorization_service, dataset_service, field_service, business_key_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = dataset_service.get_dataset_details(actor.id, key)
            editable = authorization_service.check_access(actor.id, "dataset", dataset.id, "editor")
            schema_admin = authorization_service.check_access(
                actor.id, "dataset", dataset.id, "admin"
            )
            fields = field_service.list_fields(actor.id, dataset.key)
            business_key = business_key_service.get_business_key(actor.id, dataset.key)
        except NotFoundError:
            abort(404)
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_list\") }}'>Datasets</a></nav>"
            "<h1>{{ dataset.name }}</h1><p><code>{{ dataset.key }}</code></p>"
            "<p>{{ dataset.description }}</p>"
            "<p>Initial owner: {{ dataset.owner_display_name }} "
            "(<code>{{ dataset.owner_user_id }}</code>)</p>"
            "{% if editable %}<a href='{{ url_for(\"edit_dataset\", key=dataset.key) }}'>"
            "Edit dataset</a>{% endif %}"
            "<section><h2>Fields</h2>"
            "<p>Record identifier: {% if business_key.field_keys %}"
            "{% for field_key in business_key.field_keys %}{% if not loop.first %} + {% endif %}"
            "<code>{{ field_key }}</code>{% endfor %}{% else %}Not configured{% endif %}</p>"
            '{% if schema_admin %}<a href=\'{{ url_for("new_dataset_field", '
            "key=dataset.key) }}'>Add field</a>{% endif %}"
            "{% if fields %}<table><thead><tr><th>Key</th><th>Name</th><th>Type</th>"
            "<th>Required</th><th>Unique</th>{% if schema_admin %}<th>Actions</th>"
            "{% endif %}</tr></thead><tbody>{% for field in fields %}<tr>"
            "<td><code>{{ field.key }}</code></td><td>"
            "{% if field.key in business_key.field_keys %}🔑 {% endif %}{{ field.name }}</td>"
            "<td>{{ field.data_type }}</td><td>{{ 'Yes' if field.is_required else 'No' }}</td>"
            "<td>{{ 'Yes' if field.is_unique else 'No' }}</td>"
            '{% if schema_admin %}<td><a href=\'{{ url_for("edit_dataset_field", '
            "key=dataset.key, field_key=field.key) }}'>Edit field</a></td>{% endif %}"
            "</tr>{% endfor %}</tbody></table>{% else %}<p>No fields are defined.</p>{% endif %}"
            "</section>",
            dataset=dataset,
            editable=editable,
            schema_admin=schema_admin,
            fields=fields,
            business_key=business_key,
        )

    def render_dataset_edit_form(dataset, *, name: str, description: str, csrf: str, error=None):
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_detail\", key=dataset.key) }}'>"
            "Dataset</a></nav><h1>Edit dataset</h1>"
            "<p>Key: <code>{{ dataset.key }}</code></p>"
            "{% if error %}<p role='alert'>{{ error }}</p>{% endif %}"
            "<form method='post' action='{{ url_for(\"update_dataset\", key=dataset.key) }}'>"
            "<label>Name <input name='name' value='{{ name }}'></label>"
            "<label>Description <textarea name='description'>{{ description }}</textarea></label>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Save dataset</button></form>",
            dataset=dataset,
            name=name,
            description=description,
            csrf=csrf,
            error=error,
        )

    @dataset_route("/datasets/<key>/edit", methods=["GET"])
    def edit_dataset(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = dataset_service.get_dataset_for_edit(actor.id, key)
        except NotFoundError:
            abort(404)
        return render_dataset_edit_form(
            dataset,
            name=dataset.name,
            description=dataset.description,
            csrf=login_session.csrf_token,
        )

    @dataset_route("/datasets/<key>", methods=["POST"])
    def update_dataset(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        name = request.form.get("name", "")
        description = request.form.get("description", "")
        try:
            dataset = dataset_service.update_dataset(actor.id, key, name, description)
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            try:
                dataset = dataset_service.get_dataset_for_edit(actor.id, key)
            except NotFoundError:
                abort(404)
            return (
                render_dataset_edit_form(
                    dataset,
                    name=name,
                    description=description,
                    csrf=login_session.csrf_token,
                    error=str(error),
                ),
                400,
            )
        return redirect(url_for("dataset_detail", key=dataset.key))

    def require_dataset_schema_admin(actor_id: str, key: str):
        authorization_service, dataset_service, _field_service, _business_key_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = dataset_service.get_dataset(actor_id, key)
        except NotFoundError:
            abort(404)
        if not authorization_service.check_access(actor_id, "dataset", dataset.id, "admin"):
            abort(404)
        return dataset

    def render_dataset_field_create_form(
        dataset,
        *,
        csrf: str,
        key: str = "",
        name: str = "",
        data_type: str = "string",
        is_required: bool = False,
        is_unique: bool = False,
        is_business_key_component: bool = False,
        error=None,
    ):
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_detail\", key=dataset.key) }}'>"
            "Dataset</a></nav><h1>Add field</h1>"
            "{% if error %}<p role='alert'>{{ error }}</p>{% endif %}"
            "<form method='post' action='{{ url_for(\"create_dataset_field\", "
            "key=dataset.key) }}'>"
            "<label>Key <input name='key' value='{{ key }}'></label>"
            "<label>Name <input name='name' value='{{ name }}'></label>"
            "<label>Type <select name='data_type'>"
            "{% for choice in type_choices %}<option value='{{ choice }}'"
            "{% if choice == data_type %} selected{% endif %}>{{ choice }}</option>{% endfor %}"
            "</select></label>"
            "<label><input type='checkbox' name='is_required'"
            "{% if is_required %} checked{% endif %}> Required</label>"
            "<label><input type='checkbox' name='is_unique'"
            "{% if is_unique %} checked{% endif %}> Unique</label>"
            "<label><input type='checkbox' name='is_business_key_component'"
            "{% if is_business_key_component %} checked{% endif %}> "
            "Part of Record identifier</label>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Add field</button></form>",
            dataset=dataset,
            key=key,
            name=name,
            data_type=data_type,
            is_required=is_required,
            is_unique=is_unique,
            is_business_key_component=is_business_key_component,
            error=error,
            csrf=csrf,
            type_choices=DATASET_FIELD_TYPES,
        )

    @dataset_route("/datasets/<key>/fields/new", methods=["GET"])
    def new_dataset_field(key: str):
        actor, login_session = require_authenticated()
        dataset = require_dataset_schema_admin(actor.id, key)
        return render_dataset_field_create_form(dataset, csrf=login_session.csrf_token)

    @dataset_route("/datasets/<key>/fields", methods=["POST"])
    def create_dataset_field(key: str):
        actor, login_session = require_authenticated()
        _authorization, _dataset_service, field_service, _business_key_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        field_key = request.form.get("key", "")
        name = request.form.get("name", "")
        data_type = request.form.get("data_type", "")
        is_required = "is_required" in request.form
        is_unique = "is_unique" in request.form
        is_business_key_component = "is_business_key_component" in request.form
        try:
            field_service.create_field(
                actor.id,
                key,
                field_key,
                name,
                data_type,
                is_required,
                is_unique,
                is_business_key_component,
            )
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            dataset = require_dataset_schema_admin(actor.id, key)
            response = render_dataset_field_create_form(
                dataset,
                csrf=login_session.csrf_token,
                key=field_key,
                name=name,
                data_type=data_type,
                is_required=is_required,
                is_unique=is_unique,
                is_business_key_component=is_business_key_component,
                error=str(error),
            )
            return response, 409 if isinstance(error, ConflictError) else 400
        return redirect(url_for("dataset_detail", key=key))

    def render_dataset_field_edit_form(
        dataset,
        field,
        *,
        csrf: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
        is_business_key_component: bool,
        error=None,
    ):
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_detail\", key=dataset.key) }}'>"
            "Dataset</a></nav><h1>Edit field</h1>"
            "<p>Key: <code>{{ field.key }}</code></p>"
            "{% if error %}<p role='alert'>{{ error }}</p>{% endif %}"
            "<form method='post' action='{{ url_for(\"update_dataset_field\", "
            "key=dataset.key, field_key=field.key) }}'>"
            "<label>Name <input name='name' value='{{ name }}'></label>"
            "<label>Type <select name='data_type'>"
            "{% for choice in type_choices %}<option value='{{ choice }}'"
            "{% if choice == data_type %} selected{% endif %}>{{ choice }}</option>{% endfor %}"
            "</select></label>"
            "<label><input type='checkbox' name='is_required'"
            "{% if is_required %} checked{% endif %}> Required</label>"
            "<label><input type='checkbox' name='is_unique'"
            "{% if is_unique %} checked{% endif %}> Unique</label>"
            "<label><input type='checkbox' name='is_business_key_component'"
            "{% if is_business_key_component %} checked{% endif %}> "
            "Part of Record identifier</label>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Save field</button></form>"
            "<form method='post' action='{{ url_for(\"delete_dataset_field\", "
            "key=dataset.key, field_key=field.key) }}'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Delete field</button></form>",
            dataset=dataset,
            field=field,
            name=name,
            data_type=data_type,
            is_required=is_required,
            is_unique=is_unique,
            is_business_key_component=is_business_key_component,
            error=error,
            csrf=csrf,
            type_choices=DATASET_FIELD_TYPES,
        )

    @dataset_route("/datasets/<key>/fields/<field_key>/edit", methods=["GET"])
    def edit_dataset_field(key: str, field_key: str):
        actor, login_session = require_authenticated()
        dataset = require_dataset_schema_admin(actor.id, key)
        _authorization, _dataset_service, field_service, business_key_service = (
            require_dataset_dependencies()
        )
        try:
            field = field_service.get_field(actor.id, key, field_key)
            business_key = business_key_service.get_business_key(actor.id, key)
        except NotFoundError:
            abort(404)
        return render_dataset_field_edit_form(
            dataset,
            field,
            csrf=login_session.csrf_token,
            name=field.name,
            data_type=field.data_type,
            is_required=field.is_required,
            is_unique=field.is_unique,
            is_business_key_component=field.key in business_key.field_keys,
        )

    @dataset_route("/datasets/<key>/fields/<field_key>", methods=["POST"])
    def update_dataset_field(key: str, field_key: str):
        actor, login_session = require_authenticated()
        _authorization, _dataset_service, field_service, _business_key_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        name = request.form.get("name", "")
        data_type = request.form.get("data_type", "")
        is_required = "is_required" in request.form
        is_unique = "is_unique" in request.form
        is_business_key_component = "is_business_key_component" in request.form
        try:
            field_service.update_field_and_business_key_membership(
                actor.id,
                key,
                field_key,
                name,
                data_type,
                is_required,
                is_unique,
                is_business_key_component,
            )
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            dataset = require_dataset_schema_admin(actor.id, key)
            try:
                field = field_service.get_field(actor.id, key, field_key)
            except NotFoundError:
                abort(404)
            return (
                render_dataset_field_edit_form(
                    dataset,
                    field,
                    csrf=login_session.csrf_token,
                    name=name,
                    data_type=data_type,
                    is_required=is_required,
                    is_unique=is_unique,
                    is_business_key_component=is_business_key_component,
                    error=str(error),
                ),
                400,
            )
        return redirect(url_for("dataset_detail", key=key))

    @dataset_route("/datasets/<key>/fields/<field_key>/delete", methods=["POST"])
    def delete_dataset_field(key: str, field_key: str):
        actor, login_session = require_authenticated()
        _authorization, _dataset_service, field_service, business_key_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        try:
            field_service.delete_field(actor.id, key, field_key)
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            dataset = require_dataset_schema_admin(actor.id, key)
            try:
                field = field_service.get_field(actor.id, key, field_key)
                business_key = business_key_service.get_business_key(actor.id, key)
            except NotFoundError:
                abort(404)
            return (
                render_dataset_field_edit_form(
                    dataset,
                    field,
                    csrf=login_session.csrf_token,
                    name=field.name,
                    data_type=field.data_type,
                    is_required=field.is_required,
                    is_unique=field.is_unique,
                    is_business_key_component=field.key in business_key.field_keys,
                    error=str(error),
                ),
                400,
            )
        return redirect(url_for("dataset_detail", key=key))

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
