"""Record-import wizard routes."""

from __future__ import annotations

import logging

from flask import abort, redirect, render_template, request, url_for

from refrncr.record_import import (
    MAX_UPLOAD_BYTES,
    ImportValidationError,
    SourceFormatComponentsError,
    mapping_issue_message,
)
from refrncr.services import ConflictError, NotFoundError, RecordConflictError
from refrncr.web.helpers import merge_error_messages
from refrncr.web.imports import (
    dataset_records,
    import_route,
    record_imports,
    render_authenticated_template,
    require_authenticated,
    require_csrf,
    require_dataset_dependencies,
)
from refrncr.web.imports.wizard import render_import_wizard, show_source_format_issues
from refrncr.web.routing import bounded_import_route, concept_route, upload_limit_route


def import_redirect(key, state):
    return redirect(
        url_for("imports.dataset_import_step", key=key, token=state.token, step=state.step),
        code=303,
    )


@import_route(
    "/datasets/<key>/concept/records/import-new-version",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@import_route(
    "/datasets/<key>/records/import", capability="can_maintain_dataset", methods=["GET", "POST"]
)
@upload_limit_route
@concept_route
def import_dataset_records(key: str):
    mode = "new_version"
    actor, login_session = require_authenticated()
    require_dataset_dependencies()
    dataset = dataset_records.get_dataset_for_add(actor.id, key)
    if dataset.kind == "mapping":
        mode = "mapping_update"
    reason = record_imports.eligibility(actor.id, key)
    if reason:
        raise ImportValidationError(". ".join(reason))
    state = record_imports.active(actor.id, login_session.id, key)
    if state and state.mode != mode:
        raise ImportValidationError(
            "Cancel the older staged import, then upload the complete file again."
        )
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
            mode=mode,
        )
    except ImportValidationError as error:
        return render_import_wizard(dataset, login_session, state, error=str(error), status=400)
    return import_redirect(key, state)


@import_route(
    "/datasets/<key>/records/import/<token>/step/<int:step>",
    capability="can_maintain_dataset",
    methods=["GET"],
)
@bounded_import_route
@concept_route
def dataset_import_step(key: str, token: str, step: int):
    actor, login_session = require_authenticated()
    dataset = dataset_records.get_dataset_for_add(actor.id, key)
    state = record_imports.navigate(actor.id, login_session.id, key, token, step)
    return render_import_wizard(dataset, login_session, state, step=step)


@import_route(
    "/datasets/<key>/records/import/<token>/worksheet",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@bounded_import_route
@concept_route
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


def submitted_source_format_components(mapping):
    return tuple(
        {
            "source": request.form.get(f"source_{index}", ""),
            **{
                part: request.form[f"{part}_{index}"]
                for part in ("date", "time")
                if f"{part}_{index}" in request.form
            },
            "timezone": request.form.get(f"timezone_{index}", ""),
            "fold": request.form.get(f"fold_{index}", ""),
        }
        for index, _target in enumerate(mapping)
    )


def submitted_component_names(count):
    # Components are optional only for the unchanged canonical-identifier contract.
    return {f"{part}_{index}" for part in ("date", "time") for index in range(count)} & set(
        request.form
    )


@import_route(
    "/datasets/<key>/records/import/<token>/formats",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@bounded_import_route
@concept_route
def review_source_formats(key: str, token: str):
    actor, login_session = require_authenticated()
    require_csrf(login_session.csrf_token)
    with record_imports.source_format_review(actor.id, login_session.id, key, token) as state:
        if state.mapping is None:
            raise NotFoundError("Import not found")
        column_count = len(state.mapping)
        expected = {
            f"{prefix}_{index}"
            for prefix in ("mapping", "source", "timezone", "fold")
            for index in range(column_count)
        } | {"csrf_token"}
        expected |= submitted_component_names(column_count)
        if set(request.form) != expected or any(
            len(request.form.getlist(name)) != 1 for name in expected
        ):
            abort(400)
        mapping = tuple(
            None
            if request.form[f"mapping_{index}"] == "__ignored__"
            else request.form[f"mapping_{index}"]
            for index in range(column_count)
        )
        _state, _source, assistance, issues = record_imports.submitted_source_format_assistance(
            actor.id,
            login_session.id,
            key,
            token,
            mapping,
            submitted_source_format_components(mapping),
        )
        columns = show_source_format_issues(assistance.format_columns, issues)
        return render_template(
            "pages/datasets/import/source_format_fragments.html", format_columns=columns
        )


@import_route(
    "/datasets/<key>/records/import/<token>/mapping",
    capability="can_maintain_dataset",
    methods=["GET"],
)
@bounded_import_route
@concept_route
def review_import_mapping(key: str, token: str):
    return dataset_import_step(key, token, 3)


@import_route(
    "/datasets/<key>/records/import/<token>/preview",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@bounded_import_route
@concept_route
def preview_record_import(key: str, token: str):
    actor, login_session = require_authenticated()
    state = record_imports.state(actor.id, login_session.id, key, token)
    dataset = dataset_records.get_dataset_for_add(actor.id, key)
    if state.mapping is None:
        raise NotFoundError("Import not found")
    column_count = len(state.mapping)
    if request.method == "GET":
        return dataset_import_step(key, token, 4)
    require_csrf(login_session.csrf_token)
    expected = {f"mapping_{index}" for index in range(column_count)} | {"csrf_token"}
    format_names = {
        f"{prefix}_{index}"
        for prefix in ("source", "timezone", "fold")
        for index in range(column_count)
    }
    if set(request.form) & format_names:
        expected |= format_names
        expected |= submitted_component_names(column_count)
    target_step = request.form.get("go_step")
    if target_step is not None:
        if len(request.form.getlist("go_step")) != 1 or target_step not in {"1", "2", "4"}:
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
        for index in range(column_count)
    )
    format_components = (
        submitted_source_format_components(mapping) if set(request.form) & format_names else None
    )
    try:
        preview = record_imports.preview(
            actor.id,
            login_session.id,
            key,
            token,
            mapping,
            format_components=format_components,
        )
    except SourceFormatComponentsError as error:
        return render_import_wizard(
            dataset,
            login_session,
            state,
            step=3,
            status=400,
            selected_mapping=mapping,
            source_format_issues=error.issues,
        )
    except NotFoundError:
        raise
    except Exception:
        logging.getLogger(__name__).exception("Import mapping validation failed")
        state = record_imports.state(actor.id, login_session.id, key, token)
        return render_import_wizard(
            dataset,
            login_session,
            state,
            step=3,
            error=("Validation could not be completed. No records have been imported. Try again."),
            status=500,
        )
    state = record_imports.state(actor.id, login_session.id, key, token)
    if target_step in {"1", "2"}:
        state = record_imports.navigate(actor.id, login_session.id, key, token, int(target_step))
        return import_redirect(key, state)
    if preview.non_row_errors:
        return render_import_wizard(
            dataset,
            login_session,
            state,
            step=3,
            error=merge_error_messages(
                *(mapping_issue_message(item) or item.message for item in preview.non_row_errors)
            ),
            status=400,
        )
    return import_redirect(key, state)


@import_route(
    "/datasets/<key>/records/import/<token>/confirm",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
def confirm_record_import(key: str, token: str):
    actor, login_session = require_authenticated()
    require_csrf(login_session.csrf_token)
    state = record_imports.state(actor.id, login_session.id, key, token)
    expected = (
        {"csrf_token", "confirmation"}
        if state.mode in {"new_version", "mapping_update"}
        else {"csrf_token"}
    )
    if "acknowledge_deletions" in request.form and state.mode == "new_version":
        expected.add("acknowledge_deletions")
    if set(request.form) != expected or any(len(request.form.getlist(k)) != 1 for k in expected):
        abort(400)
    dataset = dataset_records.get_dataset_for_add(actor.id, key)
    try:
        record_imports.confirm(
            actor.id,
            login_session.id,
            key,
            token,
            require_confirmation=True,
            confirmation=request.form.get("confirmation"),
            acknowledge_deletions=request.form.get("acknowledge_deletions") == "yes",
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
            error=(
                "The import changed since validation. No records have been imported. "
                "Review the validation results before continuing."
            ),
            status=409,
        )
    except Exception:
        logging.getLogger(__name__).exception("Import preview confirmation failed")
        state = record_imports.state(actor.id, login_session.id, key, token)
        return render_import_wizard(
            dataset,
            login_session,
            state,
            step=4,
            error=("The import could not be completed. No records have been imported. Try again."),
            status=500,
        )
    if state.mode == "new_version":
        return redirect(
            url_for("records.dataset_records_view", key=key, concept=True, import_results=1),
            code=303,
        )
    return import_redirect(key, record_imports.state(actor.id, login_session.id, key, token))


@import_route(
    "/datasets/<key>/records/import/<token>/cancel",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@concept_route
def cancel_record_import(key: str, token: str):
    actor, login_session = require_authenticated()
    require_csrf(login_session.csrf_token)
    if set(request.form) != {"csrf_token"} or len(request.form.getlist("csrf_token")) != 1:
        abort(400)
    try:
        record_imports.cancel(actor.id, login_session.id, key, token)
    except ConflictError as error:
        return render_authenticated_template(
            "pages/datasets/import/unavailable.html",
            error=str(error),
            return_url=url_for("imports.dataset_import_step", key=key, token=token, step=1),
        ), 409
    return redirect(url_for("records.dataset_records_view", key=key, concept=True), code=303)
