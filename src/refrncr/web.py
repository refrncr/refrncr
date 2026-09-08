# Legacy inline templates remain temporarily while pages move to file-based Jinja templates.
# ruff: noqa: E501
from __future__ import annotations

import hmac
import re
import secrets
from datetime import UTC
from decimal import Decimal
from functools import wraps
from itertools import islice
from urllib.parse import unquote, urlsplit

from flask import (
    Flask,
    abort,
    g,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from refrncr.browser_security import (
    HTTPS_ANONYMOUS_CSRF_COOKIE,
    HTTPS_SESSION_COOKIE,
    BrowserCookiePolicy,
)
from refrncr.dataset_export import ExportGenerationError, verify_artifact_readable
from refrncr.login_security import (
    MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS,
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
    canonical_account_key,
)
from refrncr.record_import import (
    DISPLAY_SAMPLE_VALUES,
    MAX_UPLOAD_BYTES,
    PREVIEW_ROWS,
    PROFILE_SCAN_ROWS,
    DatasetRecordImportService,
    ImportValidationError,
    parse_source,
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
    StaleFieldOrderError,
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


def safe_login_target(target: str | None) -> str:
    """Accept only root-relative paths, never URLs or query/fragment payloads."""
    if not target:
        return "/"
    decoded = unquote(target)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in decoded
        )
        or any(character in decoded for character in "\\%?#")
        or any(segment in {".", ".."} for segment in decoded.split("/"))
    ):
        return "/"
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or parsed.path in {"/login", "/logout", "/setup"}:
        return "/"
    return target


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

    @app.template_filter("absolute_utc")
    def absolute_utc(value):
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        months = (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
        return f"{value.day} {months[value.month - 1]} {value.year}, {value:%H:%M} UTC"

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

    authentication_cache_missing = object()

    def authenticated():
        cached = getattr(g, "authenticated_resolution", authentication_cache_missing)
        if cached is not authentication_cache_missing:
            return cached
        settings = cookie_settings()
        if settings is None:
            g.authenticated_resolution = None
            return None
        token = request.cookies.get(settings.session_name)
        if not token:
            g.authenticated_resolution = None
            return None
        resolved = sessions.resolve_with_session(token)
        if resolved is None:
            g.stale_cookie_settings = settings
        g.authenticated_resolution = resolved
        return resolved

    @app.before_request
    def redirect_anonymous_browser_request():
        # Only registered browser endpoints participate; API/health is owned by FastAPI.
        if request.endpoint and request.endpoint not in {"static", "setup", "login", "logout"}:
            if authenticated() is None:
                target = (
                    safe_login_target(request.path) if request.method in {"GET", "HEAD"} else "/"
                )
                return redirect(url_for("login", next=target))

    def active_navigation() -> str:
        endpoint = request.endpoint or ""
        if endpoint == "index":
            return "home"
        if endpoint.startswith("dataset_") or endpoint in {
            "new_dataset",
            "create_dataset",
            "edit_dataset",
            "update_dataset",
            "new_dataset_field",
            "create_dataset_field",
            "edit_dataset_field",
            "update_dataset_field",
            "delete_dataset_field",
            "import_dataset_records",
            "select_import_worksheet",
            "review_import_mapping",
            "preview_record_import",
            "confirm_record_import",
            "cancel_record_import",
            "export_dataset_records",
        }:
            return "datasets"
        if endpoint.startswith("user_"):
            return "users"
        if endpoint.startswith("team_"):
            return "teams"
        return ""

    def render_authenticated_template(template_name: str, **context):
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        csrf = resolved[1].csrf_token if resolved else None
        context.setdefault("current_actor", actor)
        context.setdefault("current_csrf", csrf)
        context.setdefault("active_navigation", active_navigation())
        context.setdefault("dataset_ui_enabled", dataset_ui_enabled)
        return render_template(template_name, **context)

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

    def render_anonymous_template(template_name: str, **context):
        context.setdefault("current_actor", None)
        context.setdefault("current_csrf", None)
        context.setdefault("active_navigation", "")
        context.setdefault("dataset_ui_enabled", dataset_ui_enabled)
        return render_template(template_name, **context)

    def anonymous_csrf_form_response(template_name: str, **context):
        settings = require_cookie_settings()
        token = secrets.token_urlsafe(32)
        context.setdefault("error", None)
        response = make_response(
            render_anonymous_template(
                template_name,
                csrf_token=token,
                **context,
            )
        )
        response.set_cookie(
            settings.anonymous_csrf_name,
            token,
            **settings.common_options,
        )
        return response

    @app.get("/")
    def index():
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        csrf = resolved[1].csrf_token if resolved else None
        return render_template(
            "pages/index.html",
            resolved=resolved,
            current_actor=actor,
            current_csrf=csrf,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets", methods=["GET"])
    def dataset_list():
        actor, _login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        visible_datasets = dataset_service.list_dataset_overview(actor.id)
        return render_template(
            "pages/datasets/list.html",
            actor=actor,
            datasets=visible_datasets,
            current_actor=actor,
            current_csrf=_login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    def render_dataset_create_form(
        *,
        key: str = "",
        name: str = "",
        description: str = "",
        error=None,
        error_field=None,
    ):
        actor, login_session = require_admin()
        return render_template(
            "pages/datasets/create.html",
            key=key,
            name=name,
            description=description,
            error=error,
            error_field=error_field,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
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
            message = str(error)
            if message.startswith("Dataset key"):
                error_field = "key"
            elif message.startswith("Dataset name"):
                error_field = "name"
            elif message.startswith("Dataset description"):
                error_field = "description"
            else:
                error_field = None
            response = render_dataset_create_form(
                key=key,
                name=name,
                description=description,
                error=message,
                error_field=error_field,
            )
            return response, 409 if isinstance(error, ConflictError) else 400
        return redirect(url_for("dataset_detail", key=dataset.key))

    @dataset_route("/datasets/<key>", methods=["GET"])
    def dataset_detail(key: str):
        actor, login_session = require_authenticated()
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
        return render_template(
            "pages/datasets/detail.html",
            dataset=dataset,
            editable=editable,
            schema_admin=schema_admin,
            fields=fields,
            business_key=business_key,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets/<key>/fields/order", methods=["POST"])
    def move_dataset_field(key: str):
        actor, login_session = require_authenticated()
        _authorization, _datasets, field_service, _business_keys, _records = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token", "expected_field_id", "field_id", "direction"}:
            abort(400, description="Invalid field order request")
        field_ids = request.form.getlist("field_id")
        directions = request.form.getlist("direction")
        expected = tuple(request.form.getlist("expected_field_id"))
        if len(field_ids) != 1 or len(directions) != 1 or not expected:
            abort(400, description="Invalid field order request")
        try:
            field_service.move_field(actor.id, key, field_ids[0], directions[0], expected)
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            abort(400, description=str(error))
        except StaleFieldOrderError as error:
            abort(409, description=str(error))
        return redirect(url_for("dataset_detail", key=key))

    def parse_page_parameter() -> int:
        values = request.args.getlist("page")
        if not values:
            return 1
        if len(values) != 1 or re.fullmatch(r"[1-9][0-9]{0,8}", values[0]) is None:
            abort(400, description="Page must be a canonical positive integer")
        return int(values[0])

    @dataset_route("/datasets/<key>/records", methods=["GET"])
    def dataset_records_view(key: str):
        actor, login_session = require_authenticated()
        (
            authorization_service,
            dataset_service,
            field_service,
            business_key_service,
            record_service,
        ) = require_dataset_dependencies()
        try:
            context = dataset_service.get_dataset_context(actor.id, key)
            dataset = context.dataset
            fields = field_service.list_fields(actor.id, key)
            identifier = business_key_service.get_business_key(actor.id, key)
            page = record_service.list_records(actor.id, key, parse_page_parameter())
            may_edit = authorization_service.check_access(actor.id, "dataset", dataset.id, "editor")
            may_configure = authorization_service.check_access(
                actor.id, "dataset", dataset.id, "admin"
            )
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
        numeric_scales = {
            field.key: max(
                (
                    max(0, -value.as_tuple().exponent)
                    for _record, values, _incomplete in rows
                    if isinstance((value := values.get(field.key)), Decimal)
                ),
                default=0,
            )
            for field in fields
            if field.data_type == "number"
        }

        def display_record_value(field, value):
            if field.data_type == "number" and isinstance(value, Decimal):
                return f"{value:.{numeric_scales[field.key]}f}"
            return value

        record_type = key[:-3] + "y" if key.endswith("ies") else key.removesuffix("s")
        range_start = (page.page - 1) * page.page_size + 1 if page.total else 0
        range_end = min(page.page * page.page_size, page.total)
        return render_template(
            "pages/datasets/records.html",
            dataset=dataset,
            context=context,
            fields=fields,
            identifier=identifier,
            rows=rows,
            page=page,
            may_edit=may_edit,
            may_configure=may_configure,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
            record_type=record_type,
            range_start=range_start,
            range_end=range_end,
            display_record_value=display_record_value,
        )

    @dataset_route("/datasets/<key>/records/export", methods=["GET"])
    def export_dataset_records(key: str):
        actor, _login_session = require_authenticated()
        _authorization, _datasets, _fields, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        formats = request.args.getlist("format")
        if len(formats) != 1 or formats[0] not in {"csv", "xlsx", "json"}:
            return "Choose exactly one supported export format", 400
        try:
            artifact = record_service.export_dataset(actor.id, key, formats[0])
        except NotFoundError:
            abort(404)
        except (ExportGenerationError, ValidationError) as error:
            return str(error), 422
        verify_artifact_readable(artifact)
        try:
            response = send_file(
                artifact.content,
                mimetype=artifact.media_type,
                as_attachment=True,
                download_name=f"{key}.{artifact.extension}",
                max_age=0,
            )
        except Exception:
            artifact.content.close()
            raise
        response.call_on_close(artifact.content.close)
        return response

    def import_route(rule, **options):
        def decorate(function):
            @wraps(function)
            def secured_import(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except (NotFoundError, AuthorizationError):
                    return "The import has expired or is unavailable. Upload the file again.", 404
                except ImportValidationError:
                    return (
                        "The import is unavailable. No records have been imported. Upload the file again.",
                        400,
                    )

            return dataset_route(rule, **options)(secured_import)

        return decorate

    def render_import_wizard(dataset, login_session, state=None, *, step=1, error=None, status=200):
        actor, _session = require_authenticated()
        fields = dataset_fields.list_fields(actor.id, dataset.key)
        source = assistance = preview = None
        required_keys = ()
        identifier_keys = ()
        mapping_errors = {}
        mapping_samples = []
        if state and state.imported_count is None:
            if step >= 3:
                state, source, assistance = record_imports.mapping_assistance(
                    actor.id, login_session.id, dataset.key, state.token
                )
            else:
                source = parse_source(state.filename, state.content, state.worksheet)
            if step >= 4:
                try:
                    preview = record_imports.saved_preview(
                        actor.id, login_session.id, dataset.key, state.token
                    )
                except NotFoundError:
                    raise
                except Exception:
                    return (
                        "Validation could not be completed. No records have been imported. Try again.",
                        503,
                    )
                state = record_imports.state(actor.id, login_session.id, dataset.key, state.token)
                if preview.non_row_errors:
                    step = 3
                    error = " ".join(item.message for item in preview.non_row_errors)
                elif step == 5 and not preview.valid:
                    step = 4
                if step != state.step:
                    state = record_imports.navigate(
                        actor.id, login_session.id, dataset.key, state.token, step
                    )
            if source.headers:
                with record_imports.uow() as uow:
                    identifier = uow.dataset_business_keys.get(dataset.id)
                identifier_keys = identifier.field_keys
                required_keys = tuple(
                    dict.fromkeys(
                        [field.key for field in fields if field.is_required]
                        + list(identifier.field_keys)
                    )
                )
        if assistance:
            # Full versions of the same bounded display examples, for accessible disclosure.
            for index in range(len(source.headers)):
                samples = (
                    row.values[index]
                    for row in source.rows[:PROFILE_SCAN_ROWS]
                    if index not in row.formulas and row.values[index].strip()
                )
                mapping_samples.append(tuple(islice(samples, DISPLAY_SAMPLE_VALUES)))
        if assistance and error:
            choices = assistance.mapping
            keys = {field.key for field in fields}
            for index, choice in enumerate(choices):
                if choice == "":
                    mapping_errors[index] = "Choose a field or Do not import."
                elif choice is not None and choice not in keys:
                    mapping_errors[index] = "Choose a field from this dataset."
                elif choice and choices.count(choice) > 1:
                    mapping_errors[index] = (
                        "This field is mapped more than once. Choose a different field or Do not import."
                    )
        success = state is not None and state.imported_count is not None
        automatic = source is not None and (source.kind == "csv" or len(source.worksheets) == 1)
        completed = {
            1: state is not None,
            2: bool(source and source.headers),
            3: bool(state and state.mapping_ready),
            4: bool(state and state.validation_valid),
            5: success,
        }
        labels = ("Upload file", "Select worksheet", "Map columns", "Preview & validate", "Import")
        steps = []
        for number, label in enumerate(labels, 1):
            done = success or completed[number]
            reachable = bool(
                state
                and not success
                and number != step
                and (done or number == 3 and source and source.headers)
                and not (number == 2 and automatic)
            )
            steps.append(dict(number=number, label=label, done=done, reachable=reachable))
        preview_fields = [field for field in fields if preview and field.key in preview.mapping]
        preview_rows = (
            [
                tuple(row.values[preview.mapping.index(field.key)] for field in preview_fields)
                for row in preview.source.rows[:PREVIEW_ROWS]
            ]
            if preview and preview.valid
            else []
        )
        report_rows = []
        if preview and preview.errors:
            rows = {row.number: row for row in preview.source.rows}
            for number, errors in preview.row_error_groups:
                row = rows[number]
                identifier_values = [
                    row.values[preview.mapping.index(key)]
                    for key in required_keys
                    if key in identifier.field_keys and key in preview.mapping
                ]
                details = []
                for item in errors:
                    value = None
                    if item.header in preview.source.headers:
                        index = preview.source.headers.index(item.header)
                        value = "[Formula]" if index in row.formulas else row.values[index]
                    details.append(dict(field=item.field, value=value, message=item.message))
                report_rows.append(
                    dict(number=number, identifier=" · ".join(identifier_values), errors=details)
                )
        back_step = 3 if step == 4 else 4 if step == 5 else 1 if automatic else step - 1
        return render_authenticated_template(
            "pages/datasets/import/wizard.html",
            dataset=dataset,
            state=state,
            step=step,
            steps=steps,
            source=source,
            assistance=assistance,
            preview=preview,
            fields=fields,
            mapping_samples=mapping_samples,
            mapping_errors=mapping_errors,
            required_keys=required_keys,
            identifier_keys=identifier_keys,
            preview_fields=preview_fields,
            preview_rows=preview_rows,
            report_rows=report_rows,
            success=success,
            automatic=automatic,
            back_step=back_step,
            error=error,
            current_csrf=login_session.csrf_token,
        ), status

    def import_redirect(key, state):
        return redirect(
            url_for("dataset_import_step", key=key, token=state.token, step=state.step), code=303
        )

    @import_route("/datasets/<key>/records/import", methods=["GET", "POST"])
    def import_dataset_records(key: str):
        actor, login_session = require_authenticated()
        require_dataset_dependencies()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state = record_imports.active(actor.id, login_session.id, key)
        if request.method == "GET":
            if state:
                state = record_imports.navigate(
                    actor.id,
                    login_session.id,
                    key,
                    state.token,
                    4 if state.mapping_ready else state.step,
                )
                return import_redirect(key, state)
            return render_import_wizard(dataset, login_session)
        require_csrf(login_session.csrf_token)
        if (
            set(request.form) != {"csrf_token"}
            or set(request.files) != {"file"}
            or len(request.files.getlist("file")) != 1
        ):
            return render_import_wizard(
                dataset, login_session, state, error="Submit exactly one import file", status=400
            )
        uploaded = request.files["file"]
        try:
            state, _source, _assistance = record_imports.begin(
                actor.id,
                login_session.id,
                key,
                uploaded.filename or "",
                uploaded.stream.read(MAX_UPLOAD_BYTES + 1),
            )
        except ImportValidationError as error:
            return render_import_wizard(dataset, login_session, state, error=str(error), status=400)
        return import_redirect(key, state)

    @import_route("/datasets/<key>/records/import/<token>/step/<int:step>", methods=["GET"])
    def dataset_import_step(key: str, token: str, step: int):
        actor, login_session = require_authenticated()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state = record_imports.navigate(actor.id, login_session.id, key, token, step)
        return render_import_wizard(dataset, login_session, state, step=step)

    @import_route("/datasets/<key>/records/import/<token>/worksheet", methods=["POST"])
    def select_import_worksheet(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        expected = {"csrf_token", "worksheet"}
        going_back = "go_step" in request.form
        if going_back:
            expected.add("go_step")
        if (
            set(request.form) != expected
            or any(len(request.form.getlist(name)) != 1 for name in expected)
            or going_back
            and request.form["go_step"] != "1"
        ):
            abort(400)
        if going_back and not request.form["worksheet"]:
            state = record_imports.navigate(actor.id, login_session.id, key, token, 1)
            return import_redirect(key, state)
        try:
            state, _source, _assistance = record_imports.select_worksheet(
                actor.id, login_session.id, key, token, request.form["worksheet"]
            )
        except ImportValidationError as error:
            dataset = dataset_records.get_dataset_for_add(actor.id, key)
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset, login_session, state, step=2, error=str(error), status=400
            )
        if going_back:
            state = record_imports.navigate(actor.id, login_session.id, key, token, 1)
        return import_redirect(key, state)

    @import_route("/datasets/<key>/records/import/<token>/mapping", methods=["GET"])
    def review_import_mapping(key: str, token: str):
        return dataset_import_step(key, token, 3)

    @import_route("/datasets/<key>/records/import/<token>/preview", methods=["GET", "POST"])
    def preview_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state, source, _assistance = record_imports.mapping_assistance(
            actor.id, login_session.id, key, token
        )
        if request.method == "GET":
            return dataset_import_step(key, token, 4)
        require_csrf(login_session.csrf_token)
        expected = {f"mapping_{index}" for index in range(len(source.headers))} | {"csrf_token"}
        target_step = request.form.get("go_step")
        if target_step is not None:
            if len(request.form.getlist("go_step")) != 1 or target_step not in {"1", "2", "4", "5"}:
                abort(400)
            # Check the original state before accepting a navigation request.
            record_imports.navigate(actor.id, login_session.id, key, token, int(target_step))
            expected.add("go_step")
        if set(request.form) != expected or any(
            len(request.form.getlist(name)) != 1 for name in expected
        ):
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                error="Submit exactly one mapping for every source column",
                status=400,
            )
        mapping = tuple(
            None
            if request.form[f"mapping_{index}"] == "__ignored__"
            else request.form[f"mapping_{index}"]
            for index in range(len(source.headers))
        )
        try:
            preview = record_imports.preview(actor.id, login_session.id, key, token, mapping)
        except NotFoundError:
            raise
        except Exception:
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                error="Validation could not be completed. No records have been imported. Try again.",
                status=503,
            )
        state = record_imports.state(actor.id, login_session.id, key, token)
        if target_step in {"1", "2"}:
            state = record_imports.navigate(
                actor.id, login_session.id, key, token, int(target_step)
            )
            return import_redirect(key, state)
        if preview.non_row_errors:
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                error=" ".join(item.message for item in preview.non_row_errors),
                status=400,
            )
        if target_step == "5" and preview.valid:
            state = record_imports.navigate(actor.id, login_session.id, key, token, 5)
        return import_redirect(key, state)

    @import_route("/datasets/<key>/records/import/<token>/confirm", methods=["POST"])
    def confirm_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token"} or len(request.form.getlist("csrf_token")) != 1:
            abort(400)
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        try:
            record_imports.confirm(
                actor.id, login_session.id, key, token, require_confirmation=True
            )
        except NotFoundError:
            raise
        except (ImportValidationError, RecordConflictError):
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=4,
                error="The import changed since validation. No records have been imported. Review the validation results before continuing.",
                status=409,
            )
        except Exception:
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=5,
                error="The import could not be completed. No records have been imported. Try again.",
                status=503,
            )
        return import_redirect(key, record_imports.state(actor.id, login_session.id, key, token))

    @import_route("/datasets/<key>/records/import/<token>/cancel", methods=["POST"])
    def cancel_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token"} or len(request.form.getlist("csrf_token")) != 1:
            abort(400)
        record_imports.cancel(actor.id, login_session.id, key, token)
        return redirect(url_for("dataset_records_view", key=key), code=303)

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
        response = render_authenticated_template(
            "pages/datasets/records/form.html",
            dataset=dataset,
            fields=fields,
            current_csrf=csrf,
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
        response = render_authenticated_template(
            "pages/datasets/records/delete.html",
            dataset=dataset,
            record=record,
            identifier=identifier,
            current_csrf=login_session.csrf_token,
            error=error,
        )
        return (response, status) if status != 200 else response

    def render_dataset_edit_form(
        dataset,
        *,
        actor,
        name: str,
        description: str,
        csrf: str,
        error=None,
        error_field=None,
    ):
        return render_template(
            "pages/datasets/edit.html",
            dataset=dataset,
            name=name,
            description=description,
            error=error,
            error_field=error_field,
            current_actor=actor,
            current_csrf=csrf,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
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
            actor=actor,
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
            message = str(error)
            if message.startswith("Dataset name"):
                error_field = "name"
            elif message.startswith("Dataset description"):
                error_field = "description"
            else:
                error_field = None
            return (
                render_dataset_edit_form(
                    dataset,
                    actor=actor,
                    name=name,
                    description=description,
                    csrf=login_session.csrf_token,
                    error=message,
                    error_field=error_field,
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
        return render_authenticated_template(
            "pages/datasets/fields/create.html",
            dataset=dataset,
            key=key,
            name=name,
            data_type=data_type,
            is_required=is_required,
            is_unique=is_unique,
            is_business_key_component=is_business_key_component,
            error=error,
            current_csrf=csrf,
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
        return render_authenticated_template(
            "pages/datasets/fields/edit.html",
            dataset=dataset,
            field=field,
            name=name,
            data_type=data_type,
            is_required=is_required,
            is_unique=is_unique,
            is_business_key_component=is_business_key_component,
            error=error,
            current_csrf=csrf,
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
            try:
                users.create_initial_admin(
                    request.form["email"],
                    request.form["display_name"],
                    request.form["password"],
                )
            except UserManagementError as error:
                message = str(error)
                if message.startswith("Display name"):
                    error_field = "display_name"
                elif message.startswith("A valid email"):
                    error_field = "email"
                elif message.startswith("Password"):
                    error_field = "password"
                else:
                    error_field = None
                return (
                    render_anonymous_template(
                        "pages/auth/setup.html",
                        csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                        error=message,
                        error_field=error_field,
                        email=request.form["email"],
                        display_name=request.form["display_name"],
                    ),
                    400,
                )
            response = redirect(url_for("login"))
            response.delete_cookie(
                settings.anonymous_csrf_name,
                **settings.common_options,
            )
            return response
        return anonymous_csrf_form_response(
            "pages/auth/setup.html",
            error_field=None,
            email=None,
            display_name=None,
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        settings = require_cookie_settings()
        return_target = safe_login_target(
            request.form.get("next") if request.method == "POST" else request.args.get("next")
        )

        def invalid_credentials_response():
            return (
                render_anonymous_template(
                    "pages/auth/login.html",
                    csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                    error=GENERIC_LOGIN_FAILURE,
                    next_target=return_target,
                    field_errors={},
                    email=request.form.get("email", "")[:MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS],
                ),
                401,
            )

        if request.method == "POST":
            require_csrf(request.cookies.get(settings.anonymous_csrf_name))
            submitted_email = request.form.get("email", "")
            submitted_password = request.form.get("password", "")
            field_errors = {}
            if not submitted_email.strip():
                field_errors["email"] = "Enter your email address."
            if not submitted_password:
                field_errors["password"] = "Enter your password."
            oversized_identifier = len(submitted_email) > MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS
            account_key = canonical_account_key(submitted_email)
            source_key = source_resolver.resolve(
                request.remote_addr,
                request.headers.get("X-Forwarded-For"),
            )
            attempt = login_limiter.reserve(source_key, account_key)
            if attempt is None:
                return invalid_credentials_response()
            if oversized_identifier:
                attempt.failed()
                return invalid_credentials_response()
            if field_errors:
                attempt.failed()
                return render_anonymous_template(
                    "pages/auth/login.html",
                    csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                    error="Check the required fields below.",
                    field_errors=field_errors,
                    email=submitted_email[:MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS],
                    next_target=return_target,
                ), 400
            permit = authentication_admission.try_acquire()
            if permit is None:
                attempt.cancel()
                return invalid_credentials_response()
            try:
                with permit:
                    principal = authentication.authenticate(submitted_email, submitted_password)
            except Exception:
                attempt.failed()
                raise
            if principal is not None:
                attempt.succeeded()
                existing_token = request.cookies.get(settings.session_name)
                if existing_token:
                    sessions.revoke(existing_token)
                token, _ = sessions.create(principal)
                response = redirect(return_target)
                response.set_cookie(
                    settings.session_name,
                    token,
                    max_age=settings.session_max_age,
                    **settings.common_options,
                )
                response.delete_cookie(settings.anonymous_csrf_name, **settings.common_options)
                return response
            attempt.failed()
            return invalid_credentials_response()
        return anonymous_csrf_form_response(
            "pages/auth/login.html", next_target=return_target, field_errors={}, email=None
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
        return render_template(
            "pages/users/list.html",
            users=managed_users,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
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
        open_team = request.args.get("open_team")
        selected_team = next((team for team in managed_teams if team.id == open_team), None)
        selected_team_members = (
            memberships.list_team_members(actor.id, selected_team.id) if selected_team else []
        )
        selected_eligible_users = (
            memberships.list_eligible_users(actor.id, selected_team.id) if selected_team else []
        )
        return render_template(
            "pages/teams/list.html",
            teams=managed_teams,
            selected_team=selected_team,
            selected_team_members=selected_team_members,
            selected_eligible_users=selected_eligible_users,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
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
        return render_template(
            "pages/teams/members.html",
            members=managed_members,
            eligible_users=eligible_users,
            team_id=team_id,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @app.post("/teams/<team_id>/members")
    def create_membership(team_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        memberships.create_membership(actor.id, request.form["user_id"], team_id)
        return redirect(url_for("team_list", open_team=team_id))

    @app.post("/teams/<team_id>/members/<membership_id>/delete")
    def delete_membership(team_id: str, membership_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        memberships.delete_membership(actor.id, team_id, membership_id)
        return redirect(url_for("team_list", open_team=team_id))

    @app.errorhandler(UserManagementError)
    def handle_user_error(error: UserManagementError):
        status = 403 if isinstance(error, AuthorizationError) else 400
        return str(error), status

    return app
