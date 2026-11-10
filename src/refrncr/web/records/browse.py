"""Record browsing, search, and export routes."""

from __future__ import annotations

import logging
from datetime import date

from flask import abort, redirect, request, send_file, url_for

from refrncr.change_status import RECORD_STATUS_FILTERS
from refrncr.dataset_export import ExportGenerationError, logged_export_stream
from refrncr.presentation import DISPLAY_STATUSES, pluralize
from refrncr.record_search import MAX_QUERY_BYTES, RecordSearchError, parse_parameters
from refrncr.services import NotFoundError, ValidationError
from refrncr.version_contracts import mapping_incomplete_message
from refrncr.web.records import (
    active_navigation,
    authorization,
    blueprint,
    dataset_versions,
    datasets,
    record_imports,
    render_authenticated_template,
    render_hierarchy,
    render_mapping_template,
    require_authenticated,
    require_csrf,
    require_dataset_dependencies,
)
from refrncr.web.routing import no_store_route, version_errors
from refrncr.web.viewmodels.records import record_value_formatter


@blueprint.dataset_route(
    "/datasets/<key>/concept/records",
    capability="can_view_dataset",
    methods=["GET"],
    defaults={"concept": True},
)
@blueprint.dataset_route(
    "/datasets/<key>/records",
    capability="can_view_dataset",
    methods=["GET"],
    defaults={"concept": False},
)
@blueprint.dataset_route(
    "/datasets/<key>/versions/<version_id>/records",
    capability="can_view_dataset",
    methods=["GET"],
    defaults={"concept": False},
)
@version_errors
@no_store_route
def dataset_records_view(key: str, version_id=None, concept=False):
    actor, login_session = require_authenticated()
    require_dataset_dependencies()
    try:
        if len(request.query_string) > MAX_QUERY_BYTES:
            raise RecordSearchError(
                "Record criteria exceed the URL limit. Clear all to start again."
            )
        if datasets.get_dataset_configuration(actor.id, key).dataset.kind == "hierarchy":
            return render_hierarchy(
                dataset_versions.read(actor.id, key, version_id, page=None, prefer_concept=concept),
                actor.id,
            )
        parameters, selected_statuses, record_status, requested_page = parse_parameters(
            request.args.items(multi=True)
        )
        if not concept and any(
            name in parameters for name in ("import_results", "status_selection")
        ):
            raise RecordSearchError("Import results require a Concept.")
        if not concept and record_status != "all":
            raise RecordSearchError("Change filters are available only in a Concept.")
        import_results = (
            concept
            and parameters.get("import_results") == "1"
            and (parameters.get("status_selection") != "1" or bool(selected_statuses))
        )
        action = parameters.get("action", "")
        view = dataset_versions.read(
            actor.id,
            key,
            version_id,
            1 if action else requested_page,
            prefer_concept=concept,
            search=parameters.get("q", ""),
            statuses=(selected_statuses or DISPLAY_STATUSES) if import_results else (),
            record_status=record_status,
            mapping_status=parameters.get("mapping_status", "all"),
        )
        criteria_parameters = {
            "mapping_status": parameters.get("mapping_status"),
            "q": view.search or None,
            "record_status": record_status if concept and record_status != "all" else None,
        }
        if import_results:
            criteria_parameters.update(import_results=1, status=selected_statuses)
        if action:
            return redirect(
                url_for(
                    "records.dataset_records_view",
                    key=key,
                    version_id=version_id,
                    concept=concept or None,
                    **criteria_parameters,
                ),
                code=303,
            )
    except RecordSearchError as error:
        # Neutral errors neither include submitted values nor render results
        # from a partially accepted query. The reset URL carries no criteria.
        return render_authenticated_template(
            "pages/datasets/search_error.html",
            error=str(error),
            clear_url=url_for(
                "records.dataset_records_view",
                key=key,
                version_id=version_id,
                concept=concept or None,
            ),
        ), 400
    except NotFoundError:
        if version_id is not None or concept:
            raise
        history = dataset_versions.history(actor.id, key)
        if history.concept:
            return redirect(
                url_for("records.dataset_records_view", key=key, concept=True), code=303
            )
        scheduled = [state for state in history.entries if state.status == "Scheduled"]
        available = [state for state in history.entries if state.status in {"Expired", "Cancelled"}]
        selected = (
            min(
                scheduled,
                key=lambda state: (state.version.effective_date, state.version.number),
            )
            if scheduled
            else max(available, key=lambda state: state.version.number, default=None)
        )
        if selected:
            if selected.status == "Cancelled":
                return redirect(url_for("versions.dataset_versions_view", key=key), code=303)
            return redirect(
                url_for("records.dataset_records_view", key=key, version_id=selected.version.id),
                code=303,
            )
        return render_authenticated_template(
            "pages/datasets/empty_records.html",
            dataset=history.context.dataset,
            may_maintain=history.may_maintain,
            may_view_access=history.may_maintain,
            may_change_owner=authorization.datasets.can_change_dataset_owner(
                actor.id, history.context.dataset.id
            ),
        )

    context, fields, identifier, page = view.context, view.fields, view.identifier, view.page
    dataset = context.dataset
    may_edit = may_configure = view.state is None and view.may_maintain
    mapping_incomplete_text = (
        mapping_incomplete_message(fields) if dataset.kind == "mapping" else None
    )
    may_add_record = (
        may_edit
        and mapping_incomplete_text is None
        and not (dataset.kind == "mapping" and dataset.mapping_mode == "linked")
    )
    rows = [
        (
            record,
            {
                field.key: value.value
                for field in fields
                for value in record.values
                if field.id == value.field_id and field.data_type == value.data_type
            },
            any(
                key not in {value.field_key for value in record.values}
                for key in identifier.field_keys
            ),
        )
        for record in page.records
    ]
    display_record_value = record_value_formatter(
        fields, (values for _record, values, _incomplete in rows)
    )

    record_type = pluralize(key, 1)
    range_start = (page.page - 1) * page.page_size + 1 if page.records else 0
    range_end = min(page.page * page.page_size, page.total) if page.records else 0

    def record_page_url(number):
        return url_for(
            "records.dataset_records_view",
            key=key,
            version_id=view.state.version.id if view.state else None,
            concept=concept or None,
            page=number,
            **criteria_parameters,
        )

    change_order = tuple(RECORD_STATUS_FILTERS)
    selected_changes = set() if record_status == "all" else set(record_status.split(","))
    record_change_options = []
    for label, value, count in zip(
        DISPLAY_STATUSES,
        change_order,
        view.status_counts,
        strict=True,
    ):
        target_changes = selected_changes ^ {value}
        target = ",".join(change for change in change_order if change in target_changes) or "all"
        record_change_options.append((label, count, value in selected_changes, target))

    return render_mapping_template(
        "pages/datasets/records.html",
        dataset=dataset,
        context=context,
        fields=fields,
        identifier=identifier,
        rows=rows,
        page=page,
        criteria_parameters=criteria_parameters,
        record_status=record_status,
        record_page_url=record_page_url,
        clear_url=url_for(
            "records.dataset_records_view", key=key, version_id=version_id, concept=concept or None
        ),
        clear_import_url=url_for(
            "records.dataset_records_view",
            key=key,
            concept=concept or None,
            q=view.search or None,
            record_status="all",
        ),
        has_criteria=bool(
            view.search
            or import_results
            or record_status != "all"
            or parameters.get("mapping_status", "all") != "all"
        ),
        mapping_status=parameters.get("mapping_status", "all"),
        mapping_record_states=view.mapping_record_states or {},
        import_unavailable=record_imports.eligibility(actor.id, key) if may_edit else None,
        import_results=import_results,
        import_totals=view.concept.import_result if view.concept and concept else None,
        result_statuses=selected_statuses,
        may_edit=may_edit,
        may_add_record=may_add_record,
        may_export=view.may_export,
        may_import=view.may_maintain,
        version_state=view.state,
        may_cancel_scheduled=view.may_maintain,
        may_view_access=view.may_maintain,
        may_change_owner=authorization.datasets.can_change_dataset_owner(actor.id, dataset.id),
        draft_in_progress=view.concept is not None,
        may_configure=may_configure,
        may_start_concept=(
            view.may_maintain
            and view.concept is None
            and view.state is not None
            and view.state.status == "Current"
        ),
        record_states=view.record_states or {},
        record_labels=view.record_labels or {},
        record_details={
            record.id: tuple(
                (field, next((v.value for v in record.values if v.field_id == field.id), None))
                for field in (
                    view.deleted_fields.get(record.id, view.baseline_fields)
                    if view.record_states.get(record.id) == "Deleted"
                    else fields
                )
            )
            for record in page.records
        },
        deleted_count=view.deleted_count,
        status_counts=view.status_counts,
        record_change_options=tuple(record_change_options),
        record_search=view.search,
        current_actor=actor,
        current_csrf=login_session.csrf_token,
        active_navigation=active_navigation(),
        dataset_ui_enabled=True,
        record_type=record_type,
        range_start=range_start,
        range_end=range_end,
        display_record_value=display_record_value,
    )


@blueprint.dataset_route(
    "/datasets/<key>/records/export/period",
    capability="can_export_dataset",
    methods=["GET", "POST"],
)
@version_errors
def export_dataset_history(key: str):
    actor, login_session = require_authenticated()
    return_url = url_for("records.dataset_records_view", key=key)
    values = {
        "valid_from": request.form.get("valid_from", ""),
        "valid_to": request.form.get("valid_to", ""),
        "open_end": request.form.get("open_end", "empty"),
        "format": request.form.get("format", "xlsx"),
    }
    error = None
    if request.method == "POST":
        require_csrf(login_session.csrf_token)
        try:
            bounds = []
            for name in ("valid_from", "valid_to"):
                raw = values[name]
                parsed = date.fromisoformat(raw) if raw else None
                if parsed is not None and parsed.isoformat() != raw:
                    raise ValueError
                bounds.append(parsed)
            if bounds[0] is not None and bounds[1] is not None and bounds[0] >= bounds[1]:
                raise ValueError
            if values["format"] not in {"csv", "xlsx"} or values["open_end"] not in {
                "empty",
                "9999",
            }:
                raise ValueError
            artifact = dataset_versions.export_history(
                actor.id,
                key,
                values["format"],
                valid_from=bounds[0],
                valid_to=bounds[1],
                open_end=values["open_end"] == "9999",
            )
            return send_file(
                artifact.content,
                mimetype=artifact.media_type,
                as_attachment=True,
                download_name=artifact.download_name,
            )
        except ValueError:
            error = "Choose a valid period and file format. From must be before To."
    return render_authenticated_template(
        "pages/datasets/export_period.html",
        values=values,
        error=error,
        current_csrf=login_session.csrf_token,
        return_url=return_url,
    ), (422 if error else 200)


@blueprint.dataset_route(
    "/datasets/<key>/concept/records/export",
    capability="can_export_dataset",
    methods=["GET"],
    defaults={"concept": True},
)
@blueprint.dataset_route(
    "/datasets/<key>/records/export",
    capability="can_export_dataset",
    methods=["GET"],
    defaults={"concept": False},
)
@blueprint.dataset_route(
    "/datasets/<key>/versions/<version_id>/records/export",
    capability="can_export_dataset",
    methods=["GET"],
    defaults={"concept": False},
)
@version_errors
def export_dataset_records(key: str, version_id=None, concept=False):
    actor, _login_session = require_authenticated()
    _authorization, _datasets, _fields, _business_keys, record_service = (
        require_dataset_dependencies()
    )
    formats = request.args.getlist("format")
    if len(formats) != 1 or formats[0] not in {"csv", "csv_zip", "xlsx", "json"}:
        return "Choose exactly one supported export format", 400
    variants = request.args.getlist("variant")
    if len(variants) > 1 or (variants and variants[0] not in {"data", "flattened", "reporting"}):
        return "Choose Data, Flattened or Reporting for a hierarchy export", 400
    try:
        artifact = record_service.export_dataset(
            actor.id,
            key,
            formats[0],
            version_id=version_id,
            prefer_concept=concept,
            variant=variants[0] if variants else None,
        )
    except NotFoundError:
        abort(404)
    except (ExportGenerationError, ValidationError) as error:
        return (
            render_authenticated_template(
                "pages/datasets/export_error.html",
                error=str(error),
                return_url=url_for(
                    "records.dataset_records_view",
                    key=key,
                    version_id=version_id,
                    concept=concept or None,
                ),
            ),
            422,
        )
    try:
        response = send_file(
            artifact.content,
            mimetype=artifact.media_type,
            as_attachment=True,
            download_name=artifact.download_name or f"{key}.{artifact.extension}",
            max_age=0,
        )
    except Exception:
        logging.getLogger(__name__).exception("Dataset export stream cleanup failed")
        artifact.content.close()
        raise
    response.response = logged_export_stream(response.response)
    response.call_on_close(artifact.content.close)
    return response
