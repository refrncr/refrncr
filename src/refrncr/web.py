# HTML templates are intentionally kept next to their small browser handlers.
# ruff: noqa: E501
from __future__ import annotations

import hmac
import re
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
from refrncr.record_import import (
    MAX_UPLOAD_BYTES,
    DatasetRecordImportService,
    ImportValidationError,
)
from refrncr.services import (
    DATASET_FIELD_TYPES,
    AuthenticationService,
    AuthorizationError,
    AuthorizationService,
    ConflictError,
    DatasetBusinessKeyService,
    DatasetFieldService,
    DatasetRecordService,
    DatasetService,
    MembershipService,
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    SessionService,
    StaleRecordError,
    TeamService,
    UserManagementError,
    UserService,
    ValidationError,
    parse_expected_revision,
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
    dataset_records: DatasetRecordService | None = None,
    record_imports: DatasetRecordImportService | None = None,
    allow_loopback_http: bool = False,
    login_limiter: LoginAttemptLimiter | None = None,
    source_resolver: SourceIdentityResolver | None = None,
    authentication_admission: BrowserAuthenticationAdmissionGuard | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BROWSER_FORM_CONTENT_LENGTH

    @app.before_request
    def import_upload_limit():
        if request.endpoint == "import_dataset_records" and request.method == "POST":
            request.max_content_length = MAX_UPLOAD_BYTES + 64 * 1024

    login_limiter = login_limiter or LoginAttemptLimiter()
    source_resolver = source_resolver or SourceIdentityResolver()
    authentication_admission = authentication_admission or BrowserAuthenticationAdmissionGuard()
    existing_dataset_dependencies = (
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
    )
    if any(dependency is not None for dependency in existing_dataset_dependencies) and not all(
        dependency is not None for dependency in existing_dataset_dependencies
    ):
        raise ValueError("Dataset browser dependencies must be supplied together")
    if all(dependency is not None for dependency in existing_dataset_dependencies):
        dataset_records = dataset_records or DatasetRecordService(datasets.uow, authorization)
        record_imports = record_imports or DatasetRecordImportService(dataset_records)
    elif dataset_records is not None:
        raise ValueError("Dataset browser dependencies must be supplied together")
    dataset_ui_enabled = dataset_records is not None
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
            or dataset_records is None
        ):
            abort(404)
        return authorization, datasets, dataset_fields, dataset_business_keys, dataset_records

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
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
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
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
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
        (
            authorization_service,
            dataset_service,
            field_service,
            business_key_service,
            record_service,
        ) = require_dataset_dependencies()
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
            "<p><a href='{{ url_for(\"dataset_records_view\", key=dataset.key) }}'>Records</a></p>"
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

    def parse_page_parameter() -> int:
        values = request.args.getlist("page")
        if not values:
            return 1
        if len(values) != 1 or re.fullmatch(r"[1-9][0-9]{0,8}", values[0]) is None:
            abort(400, description="Page must be a canonical positive integer")
        return int(values[0])

    @dataset_route("/datasets/<key>/records", methods=["GET"])
    def dataset_records_view(key: str):
        actor, _login_session = require_authenticated()
        (
            authorization_service,
            dataset_service,
            field_service,
            business_key_service,
            record_service,
        ) = require_dataset_dependencies()
        try:
            dataset = dataset_service.get_dataset(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
            identifier = business_key_service.get_business_key(actor.id, key)
            page = record_service.list_records(actor.id, key, parse_page_parameter())
            may_edit = authorization_service.check_access(actor.id, "dataset", dataset.id, "editor")
        except NotFoundError:
            abort(404)
        rows = [
            (
                record,
                {value.field_key: value.value for value in record.values},
                any(
                    key not in {value.field_key for value in record.values}
                    for key in identifier.field_keys
                ),
            )
            for record in page.records
        ]
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_detail\", key=dataset.key) }}'>Dataset</a></nav>"
            "<h1>Records — {{ dataset.name }}</h1>"
            "{% if may_edit %}<a href='{{ url_for(\"new_dataset_record\", key=dataset.key) }}'>"
            "Add record</a> <a href='{{ url_for(\"import_dataset_records\", key=dataset.key) }}'>"
            "Import records</a>{% endif %}"
            "{% if rows %}<table><thead><tr>{% for field in fields %}<th>"
            "{% if field.key in identifier.field_keys %}🔑 {% endif %}{{ field.name }}</th>"
            "{% endfor %}<th>Status</th></tr></thead><tbody>"
            "{% for record, values, incomplete in rows %}<tr>"
            "{% for field in fields %}<td>{{ values.get(field.key, '') }}</td>{% endfor %}"
            "<td>{% if incomplete %}Incomplete record{% endif %}</td>"
            '{% if may_edit %}<td><a href=\'{{ url_for("edit_dataset_record", key=dataset.key, '
            "record_id=record.id) }}'>Edit</a> <a href='{{ url_for(\"delete_dataset_record\", "
            "key=dataset.key, record_id=record.id) }}'>Delete</a></td>{% endif %}"
            "</tr>{% endfor %}</tbody></table>{% else %}<p>No records are available.</p>{% endif %}"
            '<nav>{% if page.page > 1 %}<a href=\'{{ url_for("dataset_records_view", '
            "key=dataset.key, page=page.page-1) }}'>Previous</a>{% endif %} "
            "{% if page.page * page.page_size < page.total %}<a href='{{ "
            'url_for("dataset_records_view", key=dataset.key, page=page.page+1) }}\'>Next</a>'
            "{% endif %}</nav>",
            dataset=dataset,
            fields=fields,
            identifier=identifier,
            rows=rows,
            page=page,
            may_edit=may_edit,
        )

    def render_import_upload(dataset, csrf, error=None, status=200):
        response = render_template_string(
            "<nav><a href='{{ url_for(\"dataset_records_view\", key=dataset.key) }}'>Records</a>"
            "</nav><h1>Import records</h1>{% if error %}<p role='alert'>{{ error }}</p>{% endif %}"
            "<form method='post' enctype='multipart/form-data'>"
            "<label>CSV or Excel file <input type='file' name='file' accept='.csv,.xlsx' required>"
            "</label><input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<button>Upload</button></form>",
            dataset=dataset,
            csrf=csrf,
            error=error,
        )
        return (response, status) if status != 200 else response

    def render_worksheet(dataset, state, parsed, csrf):
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_records_view\", key=dataset.key) }}'>Records</a>"
            "</nav><h1>Select worksheet</h1><p>File: {{ state.filename }}</p>"
            "<form method='post' action='{{ url_for(\"select_import_worksheet\", key=dataset.key, token=state.token) }}'>"
            "<label>Visible worksheet <select name='worksheet'>{% for sheet in parsed.worksheets %}"
            "<option value='{{ sheet }}'>{{ sheet }}</option>{% endfor %}</select></label>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'><button>Continue</button></form>",
            dataset=dataset,
            state=state,
            parsed=parsed,
            csrf=csrf,
        )

    def render_mapping(dataset, fields, state, parsed, assistance, csrf, error=None, status=200):
        response = render_template_string(
            "<nav><a href='{{ url_for(\"dataset_records_view\", key=dataset.key) }}'>Records</a>"
            "</nav><h1>Map import columns</h1>{% if error %}<p role='alert'>{{ error }}</p>{% endif %}"
            "<p>File: {{ state.filename }}</p>{% if parsed.worksheet %}<p>Worksheet: {{ parsed.worksheet }}"
            "</p>{% endif %}{% if parsed.delimiter %}<p>Detected delimiter: {{ parsed.delimiter }}</p>{% endif %}"
            "<section aria-label='Mapping completeness'><p>{{ assistance.completeness.required_mapped }} of "
            "{{ assistance.completeness.required_total }} required fields mapped.</p>"
            "{% if assistance.completeness.missing_required %}<p role='alert'>Missing required fields: "
            "{{ assistance.completeness.missing_required|join(', ') }}</p>{% endif %}"
            "{% if assistance.completeness.missing_identifier %}<p role='alert'>Missing identifier fields: "
            "{{ assistance.completeness.missing_identifier|join(', ') }}</p>{% endif %}"
            "<p>{{ assistance.completeness.unmapped_columns }} source columns unmapped; "
            "{{ assistance.completeness.ignored_columns }} source columns explicitly ignored.</p>"
            "</section>"
            "<form method='post' action='{{ url_for(\"preview_record_import\", key=dataset.key, token=state.token) }}'>"
            "{% for advice in assistance.columns %}<fieldset><legend>{{ advice.header }}</legend>"
            "{% if advice.samples %}<p>Examples: {% for sample in advice.samples %}<code>{{ sample }}</code>{% if not loop.last %}, {% endif %}{% endfor %}</p>"
            "{% else %}<p>No non-empty example values in the profiling sample.</p>{% endif %}"
            "{% if advice.candidates %}<p>Suggested: {{ advice.candidates[0].field_name }} — "
            "{{ advice.candidates[0].confidence }} confidence. {{ advice.candidates[0].reason }}.</p>{% endif %}"
            "{% if not advice.candidates %}<p>No name-based suggestion — choose a field or Ignored.</p>{% endif %}"
            "{% if advice.warning %}<p role='alert'>{{ advice.warning }}</p>{% endif %}"
            "<label>Mapping <select name='mapping_{{ loop.index0 }}'>"
            "<option value=''{% if advice.selected == '' %} selected{% endif %}>Choose a field</option>"
            "{% if advice.candidates %}<optgroup label='Suggested fields'>{% for candidate in advice.candidates %}"
            "<option value='{{ candidate.field_key }}'{% if advice.selected == candidate.field_key %} selected{% endif %}>"
            "[{{ candidate.confidence }}] {{ candidate.field_name }} — {{ candidate.reason }}</option>{% endfor %}</optgroup>{% endif %}"
            "<optgroup label='All fields'>{% for field in fields %}{% if field.key not in advice.candidate_keys %}"
            "<option value='{{ field.key }}'{% if advice.selected == field.key %} selected{% endif %}>"
            "{{ field.name }} ({{ field.key }})</option>{% endif %}{% endfor %}</optgroup>"
            "<option value='__ignored__'{% if advice.selected is none %} selected{% endif %}>Ignored</option>"
            "</select></label></fieldset>{% endfor %}"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'><button>Validate and preview</button></form>",
            dataset=dataset,
            fields=fields,
            state=state,
            parsed=parsed,
            assistance=assistance,
            csrf=csrf,
            error=error,
        )
        return (response, status) if status != 200 else response

    @dataset_route("/datasets/<key>/records/import", methods=["GET", "POST"])
    def import_dataset_records(key: str):
        actor, login_session = require_authenticated()
        _authorization, _datasets, field_service, _keys, record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
        except NotFoundError:
            abort(404)
        if request.method == "GET":
            return render_import_upload(dataset, login_session.csrf_token)
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token"} or set(request.files) != {"file"}:
            return render_import_upload(
                dataset, login_session.csrf_token, "Submit exactly one import file", 400
            )
        uploaded = request.files["file"]
        content = uploaded.stream.read(MAX_UPLOAD_BYTES + 1)
        try:
            state, source, assistance = record_imports.begin(
                actor.id, login_session.id, key, uploaded.filename or "", content
            )
            if not source.headers:
                return render_worksheet(dataset, state, source, login_session.csrf_token)
            fields = field_service.list_fields(actor.id, key)
            return render_mapping(
                dataset, fields, state, source, assistance, login_session.csrf_token
            )
        except ImportValidationError as error:
            return render_import_upload(dataset, login_session.csrf_token, str(error), 400)
        except NotFoundError:
            abort(404)

    @dataset_route("/datasets/<key>/records/import/<token>/worksheet", methods=["POST"])
    def select_import_worksheet(key: str, token: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, field_service, _keys, _records = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        if (
            set(request.form) != {"csrf_token", "worksheet"}
            or len(request.form.getlist("worksheet")) != 1
        ):
            abort(400)
        try:
            dataset = dataset_service.get_dataset(actor.id, key)
            state, source, assistance = record_imports.select_worksheet(
                actor.id, login_session.id, key, token, request.form["worksheet"]
            )
            fields = field_service.list_fields(actor.id, key)
        except NotFoundError:
            return "Import expired or unavailable; upload it again", 404
        except ImportValidationError as error:
            return str(error), 400
        return render_mapping(dataset, fields, state, source, assistance, login_session.csrf_token)

    @dataset_route("/datasets/<key>/records/import/<token>/mapping", methods=["GET"])
    def review_import_mapping(key: str, token: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, field_service, _keys, _records = (
            require_dataset_dependencies()
        )
        try:
            dataset = dataset_service.get_dataset(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
            state, source, assistance = record_imports.mapping_assistance(
                actor.id, login_session.id, key, token
            )
        except NotFoundError:
            return "Import expired or unavailable; upload it again", 404
        return render_mapping(dataset, fields, state, source, assistance, login_session.csrf_token)

    @dataset_route("/datasets/<key>/records/import/<token>/preview", methods=["GET", "POST"])
    def preview_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, field_service, _keys, _records = (
            require_dataset_dependencies()
        )
        try:
            dataset = dataset_service.get_dataset(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
            if request.method == "GET":
                preview = record_imports.saved_preview(actor.id, login_session.id, key, token)
            else:
                require_csrf(login_session.csrf_token)
                expected = {f"mapping_{index}" for index in range(250)}
                supplied = set(request.form) - {"csrf_token"}
                if not supplied or not supplied <= expected:
                    raise ImportValidationError(
                        "Submit exactly one mapping for every source column"
                    )
                indices = sorted(int(name.removeprefix("mapping_")) for name in supplied)
                if indices != list(range(len(indices))) or any(
                    len(request.form.getlist(f"mapping_{index}")) != 1 for index in indices
                ):
                    raise ImportValidationError(
                        "Submit exactly one mapping for every source column"
                    )
                mapping = tuple(
                    None
                    if request.form[f"mapping_{index}"] == "__ignored__"
                    else request.form[f"mapping_{index}"]
                    for index in indices
                )
                preview = record_imports.preview(actor.id, login_session.id, key, token, mapping)
                if preview.non_row_errors:
                    state, source, assistance = record_imports.mapping_assistance(
                        actor.id, login_session.id, key, token
                    )
                    return render_mapping(
                        dataset,
                        fields,
                        state,
                        source,
                        assistance,
                        login_session.csrf_token,
                        error=" ".join(error.message for error in preview.non_row_errors),
                        status=400,
                    )
        except NotFoundError:
            return "Import expired or unavailable; upload it again", 404
        except ImportValidationError as error:
            return str(error), 400
        mapped_headers = [
            (preview.source.headers[index], value) for index, value in enumerate(preview.mapping)
        ]
        return render_template_string(
            "<nav><a href='{{ url_for(\"dataset_records_view\", key=dataset.key) }}'>Records</a></nav>"
            "<h1>Import preview</h1><p>File: {{ preview.filename }}</p>"
            "<p><a href='{{ url_for(\"review_import_mapping\", key=dataset.key, token=token) }}'>Review mappings</a></p>"
            "{% if preview.source.worksheet %}<p>Worksheet: {{ preview.source.worksheet }}</p>{% endif %}"
            "{% if preview.source.delimiter %}<p>Detected delimiter: {{ preview.source.delimiter }}</p>{% endif %}"
            "<dl><dt>Data rows</dt><dd>{{ preview.source.rows|length }}</dd><dt>Valid rows</dt><dd>{{ preview.valid_rows }}</dd>"
            "<dt>Total failing rows</dt><dd>{{ preview.invalid_rows }}</dd><dt>Ignored empty rows</dt><dd>{{ preview.source.ignored_empty_rows }}</dd></dl>"
            "<h2>Mappings</h2><ul>{% for header, target in mapped_headers %}<li>{{ header }} → {{ target or 'Ignored' }}</li>{% endfor %}</ul>"
            "{% if preview.errors %}<h2>Errors</h2>"
            "{% if preview.non_row_errors %}<h3>File and mapping errors</h3><ul>{% for error in preview.non_row_errors %}<li>"
            "{{ error.message }}</li>{% endfor %}</ul>{% endif %}"
            "{% for row, errors in preview.row_error_groups %}<section><h3>Failing data row "
            "{{ loop.index }} — source file row {{ row }}</h3><ul>"
            "{% for error in errors %}<li>{% if error.header %}{{ error.header }}: {% endif %}"
            "{% if error.field %}{{ error.field }}: {% endif %}{{ error.message }}</li>{% endfor %}"
            "</ul></section>{% endfor %}{% if preview.row_report_truncated %}"
            "<p>Showing 100 of {{ preview.invalid_rows }} failing data rows. Row numbers above are "
            "the original source-file row numbers; row 1 is the header. Later failing rows are not "
            "shown.</p>{% endif %}{% endif %}"
            "<h2>Sample</h2><table>{% for row in preview.sample %}<tr>{% for value in row %}<td>{{ value }}</td>{% endfor %}</tr>{% endfor %}</table>"
            "{% if preview.valid %}<form method='post' action='{{ url_for(\"confirm_record_import\", key=dataset.key, token=token) }}'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'><button>Import {{ preview.valid_rows }} records</button></form>{% endif %}"
            "<form method='post' action='{{ url_for(\"cancel_record_import\", key=dataset.key, token=token) }}'>"
            "<input type='hidden' name='csrf_token' value='{{ csrf }}'><button>Cancel import</button></form>",
            dataset=dataset,
            fields=fields,
            preview=preview,
            mapped_headers=mapped_headers,
            token=token,
            csrf=login_session.csrf_token,
        )

    @dataset_route("/datasets/<key>/records/import/<token>/confirm", methods=["POST"])
    def confirm_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token"}:
            abort(400)
        try:
            record_imports.confirm(actor.id, login_session.id, key, token)
        except NotFoundError:
            return "Import expired or unavailable; upload it again", 404
        except (ImportValidationError, RecordConflictError) as error:
            return str(error), 409
        return redirect(url_for("dataset_records_view", key=key))

    @dataset_route("/datasets/<key>/records/import/<token>/cancel", methods=["POST"])
    def cancel_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        try:
            record_imports.cancel(actor.id, login_session.id, key, token)
        except NotFoundError:
            return "Import expired or unavailable; upload it again", 404
        return redirect(url_for("dataset_records_view", key=key))

    def render_record_form(
        dataset,
        fields,
        *,
        csrf,
        submitted=None,
        errors=None,
        status=200,
        record=None,
    ):
        submitted = submitted or {}
        errors = errors or {}
        response = render_template_string(
            "<nav><a href='{{ url_for(\"dataset_records_view\", key=dataset.key) }}'>Records</a>"
            "</nav><h1>{{ 'Edit record' if record else 'Add record' }}</h1>"
            "{% if errors.get('_record') %}"
            "<p role='alert'>{{ errors['_record'] }}</p>{% endif %}"
            "<form method='post' action='{{ url_for(\"edit_dataset_record\", key=dataset.key, "
            'record_id=record.id) if record else url_for("create_dataset_record", '
            "key=dataset.key) }}'>"
            "{% for field in fields %}<div><label>{{ field.name }}{% if field.is_required %} *"
            "{% endif %}{% if field.data_type == 'boolean' %}<select name='{{ field.key }}'>"
            "<option value=''>—</option><option value='true'{% if submitted.get(field.key) == "
            "'true' %} selected{% endif %}>true</option><option value='false'{% if "
            "submitted.get(field.key) == 'false' %} selected{% endif %}>false</option></select>"
            "{% else %}<input name='{{ field.key }}' type='{{ 'date' if field.data_type == 'date' "
            "else 'text' }}' value='{{ submitted.get(field.key, '') }}'>{% endif %}</label>"
            "{% if errors.get(field.key) %}<span role='alert'>{{ errors[field.key] }}</span>"
            "{% endif %}</div>{% endfor %}<input type='hidden' name='csrf_token' "
            "value='{{ csrf }}'>"
            "{% if record %}<input type='hidden' name='expected_revision' "
            "value='{{ record.revision }}'>{% endif %}"
            "<button>{{ 'Save record' if record else 'Add record' }}</button></form>",
            dataset=dataset,
            fields=fields,
            csrf=csrf,
            submitted=submitted,
            errors=errors,
            record=record,
        )
        return (response, status) if status != 200 else response

    @dataset_route("/datasets/<key>/records/new", methods=["GET"])
    def new_dataset_record(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, field_service, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
        except NotFoundError:
            abort(404)
        return render_record_form(dataset, fields, csrf=login_session.csrf_token)

    @dataset_route("/datasets/<key>/records", methods=["POST"])
    def create_dataset_record(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, field_service, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
        except NotFoundError:
            abort(404)
        expected = {field.key for field in fields}
        submitted: dict[str, str] = {}
        errors: dict[str, str] = {}
        for form_key, form_values in request.form.lists():
            if form_key == "csrf_token":
                continue
            if form_key not in expected:
                errors[form_key] = "Unknown Dataset field"
                errors["_record"] = "Record contains unknown Dataset fields"
            elif len(form_values) != 1:
                errors[form_key] = "Submit exactly one value"
            else:
                submitted[form_key] = form_values[0]
        if errors:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=errors,
                status=400,
            )
        try:
            record_service.create_record(actor.id, key, submitted)
        except RecordValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=error.errors,
                status=400,
            )
        except ValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={"_record": str(error)},
                status=400,
            )
        except RecordConflictError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={"_record": str(error)},
                status=409,
            )
        except NotFoundError:
            abort(404)
        return redirect(url_for("dataset_records_view", key=key))

    def record_form_values(record):
        values = {}
        for value in record.values:
            if value.data_type == "boolean":
                values[value.field_key] = "true" if value.value else "false"
            else:
                values[value.field_key] = str(value.value)
        return values

    def parsed_record_form(fields, infrastructure):
        expected = {field.key for field in fields}
        submitted: dict[str, str] = {}
        errors: dict[str, str] = {}
        for form_key, form_values in request.form.lists():
            if form_key in infrastructure:
                continue
            if form_key not in expected:
                errors[form_key] = "Unknown Dataset field"
                errors["_record"] = "Record contains unknown Dataset fields"
            elif len(form_values) != 1:
                errors[form_key] = "Submit exactly one value"
            else:
                submitted[form_key] = form_values[0]
        return submitted, errors

    def exact_expected_revision():
        values = request.form.getlist("expected_revision")
        if len(values) != 1:
            raise ValidationError("Submit exactly one expected revision")
        return parse_expected_revision(values[0])

    @dataset_route("/datasets/<key>/records/<record_id>/edit", methods=["GET", "POST"])
    def edit_dataset_record(key: str, record_id: str):
        actor, login_session = require_authenticated()
        _authorization, _datasets, field_service, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
            record = record_service.get_record_for_edit(actor.id, key, record_id)
        except NotFoundError:
            abort(404)
        if request.method == "GET":
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=record_form_values(record),
                record=record,
            )
        require_csrf(login_session.csrf_token)
        submitted, errors = parsed_record_form(fields, {"csrf_token", "expected_revision"})
        try:
            expected_revision = exact_expected_revision()
        except ValidationError as error:
            errors["_record"] = str(error)
        if errors:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=errors,
                status=400,
                record=record,
            )
        try:
            record_service.update_record(actor.id, key, record_id, expected_revision, submitted)
        except RecordValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=error.errors,
                status=400,
                record=record,
            )
        except ValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={"_record": str(error)},
                status=400,
                record=record,
            )
        except (RecordConflictError, StaleRecordError) as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={"_record": str(error)},
                status=409,
                record=record,
            )
        except NotFoundError:
            abort(404)
        return redirect(url_for("dataset_records_view", key=key))

    @dataset_route("/datasets/<key>/records/<record_id>/delete", methods=["GET", "POST"])
    def delete_dataset_record(key: str, record_id: str):
        actor, login_session = require_authenticated()
        _authorization, _datasets, _fields, business_keys, record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            record = record_service.get_record_for_edit(actor.id, key, record_id)
            identifier_keys = business_keys.get_business_key(actor.id, key).field_keys
        except NotFoundError:
            abort(404)
        values = {value.field_key: value.value for value in record.values}
        incomplete = any(field_key not in values for field_key in identifier_keys)
        identifier = (
            "Incomplete record"
            if incomplete
            else " / ".join(str(values[field_key]) for field_key in identifier_keys)
        )
        error = None
        status = 200
        if request.method == "POST":
            require_csrf(login_session.csrf_token)
            try:
                if request.form.getlist("confirm") != ["yes"]:
                    raise ValidationError("Explicit confirmation is required")
                expected_revision = exact_expected_revision()
                extras = set(request.form) - {"csrf_token", "confirm", "expected_revision"}
                if extras:
                    raise ValidationError("Unknown form field")
                record_service.delete_record(actor.id, key, record_id, expected_revision)
            except ValidationError as exc:
                error, status = str(exc), 400
            except StaleRecordError as exc:
                error, status = str(exc), 409
            except NotFoundError:
                abort(404)
            else:
                return redirect(url_for("dataset_records_view", key=key))
        response = render_template_string(
            "<nav><a href='{{ url_for(\"dataset_records_view\", key=dataset.key) }}'>Records</a>"
            "</nav><h1>Delete record</h1>{% if error %}<p role='alert'>{{ error }}</p>{% endif %}"
            "<p>Record identifier: {{ identifier }}</p><p>This deletion is permanent.</p>"
            "<form method='post'><input type='hidden' name='csrf_token' value='{{ csrf }}'>"
            "<input type='hidden' name='expected_revision' value='{{ record.revision }}'>"
            "<label><input type='checkbox' name='confirm' value='yes'> Confirm permanent "
            "deletion</label><button>Delete record</button></form>",
            dataset=dataset,
            record=record,
            identifier=identifier,
            csrf=login_session.csrf_token,
            error=error,
        )
        return (response, status) if status != 200 else response

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
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
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
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
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
        (
            authorization_service,
            dataset_service,
            _field_service,
            _business_key_service,
            _record_service,
        ) = require_dataset_dependencies()
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
        _authorization, _dataset_service, field_service, _business_key_service, _record_service = (
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
        _authorization, _dataset_service, field_service, business_key_service, record_service = (
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
        _authorization, _dataset_service, field_service, _business_key_service, _record_service = (
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
        _authorization, _dataset_service, field_service, business_key_service, record_service = (
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
        if record_imports is not None:
            record_imports.revoke_session(resolved[1].id)
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
