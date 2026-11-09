"""Dataset export-access and field-order routes."""

from flask import abort, g, redirect, request, url_for

from refrncr.services import (
    ConflictError,
    NotFoundError,
    StaleFieldOrderError,
    ValidationError,
)
from refrncr.web.datasets import (
    blueprint,
    dataset_versions,
    require_authenticated,
    require_csrf,
    require_dataset_dependencies,
)
from refrncr.web.datasets.core import render_dataset_configuration
from refrncr.web.routing import concept_route, dataset_not_found_route


def access_management_target(key):
    """Where export access changes return: the dataset's Access tab."""
    concept = request.args.get("concept") == "1"
    version_id = request.args.get("version_id") or None
    return (
        concept,
        version_id,
        url_for(
            "datasets.dataset_detail",
            key=key,
            version_id=version_id,
            concept=concept or None,
            tab="access-management",
            _anchor="access-management",
        ),
    )


@blueprint.dataset_route(
    "/datasets/<key>/export-access",
    capability="can_manage_export_access",
    methods=["POST"],
)
@dataset_not_found_route
def dataset_export_access(key):
    actor, login_session = require_authenticated()
    _auth, service, *_rest = require_dataset_dependencies()
    require_csrf(login_session.csrf_token)
    concept, version_id, target = access_management_target(key)
    try:
        expected = {"csrf_token", "subject"}
        if set(request.form) != expected or any(
            len(request.form.getlist(item)) != 1 for item in expected
        ):
            raise ValidationError("Choose one user or team.")
        subject_type, separator, subject_id = request.form["subject"].partition(":")
        if not separator:
            raise ValidationError("Choose one user or team.")
        service.grant_export_access(actor.id, key, subject_type, subject_id)
    except (ValidationError, ConflictError) as error:
        # Show the error on the Access management tab the form came from.
        if concept:
            try:
                g.concept_ui = dataset_versions.concept_context(actor.id, key)
            except (NotFoundError, ConflictError):
                concept = False
        response = render_dataset_configuration(
            key,
            concept=concept,
            version_id=version_id,
            access_error=str(error),
            access_subject=request.form.get("subject", ""),
        )
        return response, 409 if isinstance(error, ConflictError) else 400
    return redirect(target)


@blueprint.dataset_route(
    "/datasets/<key>/export-access/<access_id>/revoke",
    capability="can_manage_export_access",
    methods=["POST"],
)
@dataset_not_found_route
def revoke_dataset_export_access(key, access_id):
    actor, login_session = require_authenticated()
    _auth, service, *_rest = require_dataset_dependencies()
    require_csrf(login_session.csrf_token)
    service.revoke_export_access(actor.id, key, access_id)
    return redirect(access_management_target(key)[2])


@blueprint.dataset_route(
    "/datasets/<key>/fields/order",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
def move_dataset_field(key: str):
    actor, login_session = require_authenticated()
    _authorization, _datasets, field_service, _business_keys, _records = (
        require_dataset_dependencies()
    )
    require_csrf(login_session.csrf_token)
    if set(request.form) == {"csrf_token", "expected_field_id", "ordered_field_id"}:
        # Drag and keyboard reordering (field-order.js) submit the complete new order.
        expected = tuple(request.form.getlist("expected_field_id"))
        proposed = tuple(request.form.getlist("ordered_field_id"))
        try:
            field_service.replace_order(actor.id, key, expected, proposed)
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            abort(400, description=str(error))
        except StaleFieldOrderError as error:
            abort(409, description=str(error))
        return "", 204
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
    return redirect(
        url_for("datasets.dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
    )
