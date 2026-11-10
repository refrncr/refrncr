"""Hierarchy import workflow routes."""

from flask import abort, redirect, request, url_for

from refrncr.record_import import MAX_UPLOAD_BYTES, ImportValidationError
from refrncr.services import ConflictError, StaleRecordError
from refrncr.web.hierarchies import (
    blueprint,
    dataset_records,
    hierarchy_import_service,
    render_authenticated_template,
    require_authenticated,
    require_csrf,
)
from refrncr.web.routing import import_route


@import_route(
    blueprint,
    "/datasets/<key>/concept/hierarchy-import",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
def import_hierarchy_file(key: str):
    hierarchy_imports = hierarchy_import_service()
    if hierarchy_imports is None:
        abort(404)
    actor, login_session = require_authenticated()
    dataset = dataset_records.get_dataset_for_add(actor.id, key)
    if dataset.kind != "hierarchy":
        abort(404)
    state = hierarchy_imports.active(actor.id, login_session.id, key)
    if request.method == "GET":
        preview = (
            hierarchy_imports.preview(actor.id, login_session.id, key, state.token)
            if state
            else None
        )
        return render_authenticated_template(
            "pages/datasets/hierarchy_import.html",
            dataset=dataset,
            state=state,
            preview=preview,
            current_csrf=login_session.csrf_token,
        )
    require_csrf(login_session.csrf_token)
    if (
        set(request.form) != {"csrf_token"}
        or set(request.files) != {"file"}
        or len(request.files.getlist("file")) != 1
    ):
        return render_authenticated_template(
            "pages/datasets/hierarchy_import.html",
            dataset=dataset,
            state=state,
            preview=None,
            current_csrf=login_session.csrf_token,
            error="Submit exactly one Excel workbook",
        ), 400
    uploaded = request.files["file"]
    try:
        state = hierarchy_imports.begin(
            actor.id,
            login_session.id,
            key,
            uploaded.filename or "",
            uploaded.stream.read(MAX_UPLOAD_BYTES + 1),
        )
    except ImportValidationError as error:
        return render_authenticated_template(
            "pages/datasets/hierarchy_import.html",
            dataset=dataset,
            state=state,
            preview=None,
            current_csrf=login_session.csrf_token,
            error=str(error),
        ), 400
    return redirect(
        url_for("hierarchies.hierarchy_import_preview", key=key, token=state.token), code=303
    )


@import_route(
    blueprint,
    "/datasets/<key>/concept/hierarchy-import/<token>",
    capability="can_maintain_dataset",
    methods=["GET"],
)
def hierarchy_import_preview(key: str, token: str):
    hierarchy_imports = hierarchy_import_service()
    if hierarchy_imports is None:
        abort(404)
    actor, login_session = require_authenticated()
    dataset = dataset_records.get_dataset_for_add(actor.id, key)
    state = hierarchy_imports.state(actor.id, login_session.id, key, token)
    preview = hierarchy_imports.preview(actor.id, login_session.id, key, token)
    return render_authenticated_template(
        "pages/datasets/hierarchy_import.html",
        dataset=dataset,
        state=state,
        preview=preview,
        current_csrf=login_session.csrf_token,
    )


@import_route(
    blueprint,
    "/datasets/<key>/concept/hierarchy-import/<token>/confirm",
    capability="can_maintain_dataset",
    methods=["POST"],
)
def confirm_hierarchy_import(key: str, token: str):
    hierarchy_imports = hierarchy_import_service()
    if hierarchy_imports is None:
        abort(404)
    actor, login_session = require_authenticated()
    require_csrf(login_session.csrf_token)
    expected = {"csrf_token", "confirmation"}
    if "acknowledge_removals" in request.form:
        expected.add("acknowledge_removals")
    if set(request.form) != expected or any(
        len(request.form.getlist(name)) != 1 for name in expected
    ):
        abort(400)
    try:
        hierarchy_imports.confirm(
            actor.id,
            login_session.id,
            key,
            token,
            confirmation=request.form["confirmation"],
            acknowledge_removals=request.form.get("acknowledge_removals") == "yes",
        )
    except (ImportValidationError, StaleRecordError, ConflictError) as error:
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state = hierarchy_imports.state(actor.id, login_session.id, key, token)
        preview = hierarchy_imports.preview(actor.id, login_session.id, key, token)
        return render_authenticated_template(
            "pages/datasets/hierarchy_import.html",
            dataset=dataset,
            state=state,
            preview=preview,
            current_csrf=login_session.csrf_token,
            error=str(error),
        ), 409
    return redirect(url_for("records.dataset_records_view", key=key, concept=True), code=303)


@import_route(
    blueprint,
    "/datasets/<key>/concept/hierarchy-import/<token>/cancel",
    capability="can_maintain_dataset",
    methods=["POST"],
)
def cancel_hierarchy_import(key: str, token: str):
    hierarchy_imports = hierarchy_import_service()
    if hierarchy_imports is None:
        abort(404)
    actor, login_session = require_authenticated()
    require_csrf(login_session.csrf_token)
    if set(request.form) != {"csrf_token"} or len(request.form.getlist("csrf_token")) != 1:
        abort(400)
    try:
        hierarchy_imports.cancel(actor.id, login_session.id, key, token)
    except ConflictError as error:
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state = hierarchy_imports.active(actor.id, login_session.id, key)
        return render_authenticated_template(
            "pages/datasets/hierarchy_import.html",
            dataset=dataset,
            state=state,
            preview=None,
            current_csrf=login_session.csrf_token,
            error=str(error),
        ), 409
    return redirect(url_for("records.dataset_records_view", key=key, concept=True), code=303)
