"""Dataset metadata, deletion, and field-maintenance routes."""

from flask import abort, g, redirect, request, url_for

from refrncr.datatypes import DATATYPES
from refrncr.ports import PersistenceConflictError
from refrncr.services import ConflictError, NotFoundError, ValidationError
from refrncr.web.datasets import (
    audit_security,
    blueprint,
    dataset_versions,
    datasets,
    render_authenticated_template,
    render_mapping_template,
    require_authenticated,
    require_csrf,
    require_dataset_dependencies,
)
from refrncr.web.datasets.core import dataset_settings_redirect, render_dataset_configuration
from refrncr.web.routing import concept_route


@blueprint.dataset_route(
    "/datasets/<key>/edit",
    capability="can_maintain_dataset",
    methods=["GET"],
)
def edit_dataset(key: str):
    actor, login_session = require_authenticated()
    _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
        require_dataset_dependencies()
    )
    try:
        dataset = dataset_service.get_dataset_for_edit(actor.id, key)
    except NotFoundError:
        abort(404)
    return dataset_settings_redirect(dataset.key, edit_mode="all")


@blueprint.dataset_route(
    "/datasets/<key>",
    capability="can_maintain_dataset",
    methods=["POST"],
)
def update_dataset(key: str):
    actor, login_session = require_authenticated()
    _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
        require_dataset_dependencies()
    )
    require_csrf(login_session.csrf_token)
    edit_mode = request.form.get("edit_section")
    if edit_mode not in (None, "general", "ownership"):
        abort(400)
    return_context = request.form.get("settings_return_context", "detail")
    if return_context not in ("concept", "detail", "version"):
        abort(400)
    return_concept = return_context == "concept"
    return_version_id = request.form.get("settings_return_version_id") or None
    if return_context != "version" and return_version_id:
        abort(400)
    try:
        current = dataset_service.get_dataset_for_edit(actor.id, key)
        if edit_mode == "ownership":
            name, description, icon, technical_key = (
                current.name,
                current.description,
                None,
                None,
            )
        else:
            name = request.form.get("dataset_label", request.form.get("name", current.name))
            description = request.form.get("description", current.description)
            icon = request.form.get("icon")
            technical_key = request.form.get("technical_key")
        owner_change = None
        if edit_mode in (None, "ownership") and {
            "owner_team_id",
            "expected_owner_team_id",
        } & set(request.form):
            if any(
                len(request.form.getlist(field)) != 1
                for field in ("owner_team_id", "expected_owner_team_id")
            ):
                raise ValidationError(
                    "Submit exactly one expected and new owning team. Reopen the form."
                )
            owner_change = (
                request.form["expected_owner_team_id"],
                request.form["owner_team_id"],
            )
        dataset = dataset_service.update_dataset(
            actor.id,
            key,
            name,
            description,
            owner_change=owner_change,
            icon=icon,
            new_key=technical_key,
        )
    except NotFoundError:
        abort(404)
    except (ValidationError, ConflictError) as error:
        try:
            dataset = dataset_service.get_dataset_for_edit(actor.id, key)
        except NotFoundError:
            abort(404)
        message = str(error)
        error_field = {"key": "technical_key"}.get(error.field, error.field)
        if return_concept:
            try:
                g.concept_ui = dataset_versions.concept_context(actor.id, dataset.key)
            except (NotFoundError, ConflictError):
                return_concept = False
        return (
            render_dataset_configuration(
                dataset.key,
                concept=return_concept,
                version_id=return_version_id,
                edit_mode=edit_mode or "all",
                name=name,
                description=description,
                error=message,
                error_field=error_field,
            ),
            409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(
        url_for(
            "datasets.dataset_detail",
            key=dataset.key,
            version_id=return_version_id,
            concept=True if return_concept else None,
            tab="dataset-details",
            _anchor="dataset-details",
        )
    )


@blueprint.dataset_route(
    "/datasets/id/<dataset_id>/delete",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
def dataset_delete(dataset_id: str):
    actor, login_session = require_authenticated()
    _auth, service, *_rest = require_dataset_dependencies()
    error = None
    status = 200
    try:
        if request.method == "POST":
            require_csrf(login_session.csrf_token)
            # A duplicate/missing field cannot select a favorable value.
            name = request.form.getlist("confirmation_name")
            expected = request.form.getlist("expected_updated_at")
            service.delete_dataset(
                actor.id,
                dataset_id,
                name[0] if len(name) == 1 else None,
                expected[0] if len(expected) == 1 else None,
            )
            return redirect(url_for("datasets.dataset_list", deleted="1"), code=303)
    except NotFoundError:
        abort(404)
    except (ValidationError, ConflictError) as failure:
        error = str(failure)
        status = 409 if isinstance(failure, ConflictError) else 400
    except PersistenceConflictError:
        error = "Dataset deletion could not be completed. Reopen the confirmation and try again."
        status = 409
    try:
        context = service.get_dataset_for_deletion(actor.id, dataset_id)
    except NotFoundError:
        abort(404)
    except PersistenceConflictError:
        abort(503, description="Dataset confirmation is temporarily unavailable.")
    return render_authenticated_template(
        "pages/datasets/delete.html",
        deletion=context,
        error=error,
    ), status


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
    if not authorization_service.has_capability(actor_id, "can_maintain_dataset", dataset.id):
        audit_security("security.authorization_denied", actor_user_id=actor_id)
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
    actor, _login_session = require_authenticated()
    _authorization, dataset_service, field_service, *_rest = require_dataset_dependencies()
    mapping_fields = (
        field_service.list_fields(actor.id, dataset.key) if dataset.kind == "mapping" else ()
    )
    mapping_source_fields = ()
    if dataset.kind == "mapping":
        source = dataset_service.mapping_source(actor.id, dataset.key)
        existing = {field.source_field_key for field in mapping_fields}
        mapping_source_fields = tuple(
            field for field in source["fields"] if field.key not in existing
        )
    selected_mapping_role = request.form.get(
        "mapping_field_role", request.args.get("role", "mapping_target")
    )
    if selected_mapping_role not in {"source_context", "mapping_target"}:
        selected_mapping_role = "mapping_target"
    return render_mapping_template(
        "pages/datasets/fields/create.html",
        definition=request.form.get("definition", ""),
        unit=request.form.get("unit", ""),
        dataset=dataset,
        key=key,
        name=name,
        data_type=data_type,
        is_required=is_required,
        is_unique=is_unique,
        is_business_key_component=is_business_key_component,
        error=error,
        current_csrf=csrf,
        type_choices=tuple(DATATYPES.values()),
        mapping_field_role=selected_mapping_role,
        mapping_source_field_key=request.form.get("source_field_key", ""),
        mapping_source_fields=mapping_source_fields,
        hierarchy_scope=request.form.get("hierarchy_scope", "leaves"),
    )


@blueprint.dataset_route(
    "/datasets/<key>/fields/new",
    capability="can_maintain_dataset",
    methods=["GET"],
)
@concept_route
def new_dataset_field(key: str):
    actor, login_session = require_authenticated()
    dataset = require_dataset_schema_admin(actor.id, key)
    return render_dataset_field_create_form(dataset, csrf=login_session.csrf_token)


@blueprint.dataset_route(
    "/datasets/<key>/fields",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
def create_dataset_field(key: str):
    actor, login_session = require_authenticated()
    _authorization, _dataset_service, field_service, _business_key_service, _record_service = (
        require_dataset_dependencies()
    )
    require_csrf(login_session.csrf_token)
    field_key = request.form.get("key", "")
    name = request.form.get("field_label", request.form.get("name", ""))
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
            definition=request.form.get("definition", ""),
            unit=request.form.get("unit", ""),
            mapping_role=request.form.get("mapping_field_role") or None,
            source_field_key=request.form.get("source_field_key") or None,
            hierarchy_scope=request.form.get("hierarchy_scope") or None,
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
    return redirect(
        url_for("datasets.dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
    )


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
    key_value: str,
    may_edit_key: bool,
    error=None,
):
    return render_authenticated_template(
        "pages/datasets/fields/edit.html",
        definition=request.form.get("definition", field.definition),
        unit=request.form.get("unit", field.unit),
        dataset=dataset,
        field=field,
        name=name,
        data_type=data_type,
        is_required=is_required,
        is_unique=is_unique,
        is_business_key_component=is_business_key_component,
        key_value=key_value,
        may_edit_key=may_edit_key,
        error=error,
        current_csrf=csrf,
        type_choices=tuple(DATATYPES.values()),
        hierarchy_scope=request.form.get("hierarchy_scope", field.hierarchy_scope),
    )


@blueprint.dataset_route(
    "/datasets/<key>/fields/<field_key>/edit",
    capability="can_maintain_dataset",
    methods=["GET"],
)
@concept_route
def edit_dataset_field(key: str, field_key: str):
    actor, login_session = require_authenticated()
    dataset = require_dataset_schema_admin(actor.id, key)
    _authorization, _dataset_service, field_service, business_key_service, record_service = (
        require_dataset_dependencies()
    )
    try:
        field = field_service.get_field(actor.id, key, field_key)
        business_key = business_key_service.get_business_key(actor.id, key)
        may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
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
        key_value=field.key,
        may_edit_key=may_edit_key,
    )


@blueprint.dataset_route(
    "/datasets/<key>/fields/<field_key>",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
def update_dataset_field(key: str, field_key: str):
    actor, login_session = require_authenticated()
    _authorization, _dataset_service, field_service, _business_key_service, _record_service = (
        require_dataset_dependencies()
    )
    require_csrf(login_session.csrf_token)
    name = request.form.get("field_label", request.form.get("name", ""))
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
            new_key=request.form.get("key", field_key),
            definition=request.form.get("definition", ""),
            unit=request.form.get("unit", ""),
            hierarchy_scope=request.form.get("hierarchy_scope") or None,
        )
    except NotFoundError:
        abort(404)
    except (ValidationError, ConflictError) as error:
        dataset = require_dataset_schema_admin(actor.id, key)
        try:
            field = field_service.get_field(actor.id, key, field_key)
            may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
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
                key_value=request.form.get("key", field.key),
                may_edit_key=may_edit_key,
                error=str(error),
            ),
            409 if isinstance(error, ConflictError) else 400,
        )
    return redirect(
        url_for("datasets.dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
    )


@blueprint.dataset_route(
    "/datasets/<key>/fields/<field_key>/delete",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
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
            may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
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
                key_value=field.key,
                may_edit_key=may_edit_key,
                error=str(error),
            ),
            400,
        )
    return redirect(
        url_for("datasets.dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
    )
