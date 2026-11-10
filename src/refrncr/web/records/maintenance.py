"""Record create, edit, restore, and delete routes."""

from __future__ import annotations

from flask import abort, redirect, request, url_for

from refrncr.datatypes import get_datatype
from refrncr.presentation import pluralize, record_label
from refrncr.services import (
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    StaleRecordError,
    ValidationError,
)
from refrncr.version_contracts import mapping_incomplete_message
from refrncr.web.records import (
    blueprint,
    dataset_records,
    exact_expected_revision,
    render_authenticated_template,
    require_authenticated,
    require_csrf,
    require_dataset_dependencies,
)
from refrncr.web.routing import concept_route


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
    record_type = pluralize(dataset.key, 1)
    response = render_authenticated_template(
        "pages/datasets/records/form.html",
        dataset=dataset,
        fields=fields,
        current_csrf=csrf,
        submitted=submitted,
        errors=errors,
        record=record,
        record_type=record_type,
    )
    return (response, status) if status != 200 else response


@blueprint.dataset_route(
    "/datasets/<key>/records/new",
    capability="can_maintain_dataset",
    methods=["GET"],
)
@concept_route
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
    if dataset.kind == "mapping" and (
        dataset.mapping_mode == "linked" or mapping_incomplete_message(fields)
    ):
        abort(404)
    return render_record_form(dataset, fields, csrf=login_session.csrf_token)


@blueprint.dataset_route(
    "/datasets/<key>/records",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
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
    submitted, errors = parsed_record_form(fields, {"csrf_token"})
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
            errors={error.field_key or "_record": str(error)},
            status=409,
        )
    except NotFoundError:
        abort(404)
    return redirect(url_for("records.dataset_records_view", key=key, concept=True))


def record_form_values(record):
    return {
        value.field_key: get_datatype(value.data_type).text(value.value) for value in record.values
    }


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


@blueprint.dataset_route(
    "/datasets/<key>/records/<record_id>/edit",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@concept_route
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
    submitted, errors = parsed_record_form(
        fields, {"csrf_token", "expected_revision", "return_focus"}
    )
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
    focus_mode = "keyboard" if request.form.getlist("return_focus") == ["keyboard"] else "pointer"
    return redirect(
        url_for(
            "records.dataset_records_view",
            key=key,
            concept=True,
            focus_record=record_id,
            focus_mode=focus_mode,
        )
    )


@blueprint.dataset_route(
    "/datasets/<key>/concept/records/<record_id>/restore",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
def restore_dataset_record(key: str, record_id: str):
    actor, login_session = require_authenticated()
    require_csrf(login_session.csrf_token)
    expected = {"csrf_token", "expected_revision", "concept_id"}
    if set(request.form) != expected or any(len(request.form.getlist(k)) != 1 for k in expected):
        abort(400)
    try:
        dataset_records.restore_record(
            actor.id,
            key,
            record_id,
            exact_expected_revision(),
            concept_id=request.form["concept_id"],
        )
    except (ValidationError, RecordConflictError, StaleRecordError) as error:
        return render_authenticated_template(
            "pages/datasets/records/restore_error.html", error=str(error), key=key
        ), 409
    except NotFoundError:
        abort(404)
    return redirect(url_for("records.dataset_records_view", key=key, concept=True), code=303)


@blueprint.dataset_route(
    "/datasets/<key>/records/<record_id>/delete",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@concept_route
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
    values = {value.field_key: value for value in record.values}
    identifier_parts = [
        get_datatype(values[field_key].data_type).text(values[field_key].value)
        if field_key in values
        else None
        for field_key in identifier_keys
    ]
    identifier = record_label(identifier_parts, sentence=True)
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
            return redirect(url_for("records.dataset_records_view", key=key, concept=True))
    response = render_authenticated_template(
        "pages/datasets/records/delete.html",
        dataset=dataset,
        record=record,
        identifier=identifier,
        current_csrf=login_session.csrf_token,
        error=error,
    )
    return (response, status) if status != 200 else response
