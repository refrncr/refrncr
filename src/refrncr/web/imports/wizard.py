"""Rendering support for the record-import wizard."""

from __future__ import annotations

import logging

from flask import request

from refrncr.presentation import record_label
from refrncr.record_import import (
    PROFILE_SCAN_ROWS,
    ImportValidationError,
    distinct_samples,
    mapping_issue_message,
    parse_source,
)
from refrncr.services import NotFoundError
from refrncr.web.helpers import merge_error_messages, parse_page_parameter
from refrncr.web.imports import (
    record_imports,
    render_authenticated_template,
    require_authenticated,
)
from refrncr.web.viewmodels.imports import import_wizard_status


def show_source_format_issues(columns, issues):
    columns = list(columns)
    for issue in issues:
        if issue.column_index is None or issue.column_index >= len(columns):
            continue
        column = columns[issue.column_index]
        if column is not None:
            message = mapping_issue_message(issue)
            columns[issue.column_index] = {
                **column,
                "state": "failing",
                "verdict": message,
                "error": message,
            }
    return columns


def render_import_wizard(
    dataset,
    login_session,
    state=None,
    *,
    step=1,
    error=None,
    status=200,
    selected_mapping=None,
    source_format_issues=(),
):
    actor, _session = require_authenticated()
    mode = state.mode if state else "mapping_update" if dataset.kind == "mapping" else "new_version"
    fields = ()
    source = assistance = preview = None
    required_keys = ()
    identifier_keys = ()
    mapping_errors = {}
    step_mapping_errors = []
    mapping_samples = []
    format_columns = []
    if state and state.imported_count is None:
        if step >= 3:
            state, source, assistance = record_imports.mapping_assistance(
                actor.id,
                login_session.id,
                dataset.key,
                state.token,
                selected_mapping,
                include_formats=True,
            )
            fields = assistance.fields
            required_keys = assistance.required_keys
            identifier_keys = assistance.identifier_keys
            if error or source_format_issues:
                for issue in assistance.errors:
                    message = mapping_issue_message(issue)
                    if issue.column_index is None:
                        if message:
                            step_mapping_errors.append(message)
                    elif message:
                        mapping_errors[issue.column_index] = message
        else:
            source = parse_source(state.filename, state.content, state.worksheet)
        if step >= 4:
            try:
                preview = record_imports.saved_preview(
                    actor.id,
                    login_session.id,
                    dataset.key,
                    state.token,
                    page=parse_page_parameter(),
                    statuses=tuple(value for value in request.args.getlist("status") if value),
                    search=request.args.get("q", ""),
                )
            except (NotFoundError, ImportValidationError):
                raise
            except Exception:
                logging.getLogger(__name__).exception("Import preview validation failed")
                return (
                    "Validation could not be completed. No records have been imported. Try again.",
                    500,
                )
            state = record_imports.state(actor.id, login_session.id, dataset.key, state.token)
            if preview.non_row_errors:
                step = 3
                preview_messages = [
                    mapping_issue_message(item) or item.message for item in preview.non_row_errors
                ]
                error = " ".join(dict.fromkeys(preview_messages))
            elif step == 4 and not preview.valid:
                step = 4
            if step != state.step:
                state = record_imports.navigate(
                    actor.id, login_session.id, dataset.key, state.token, step
                )
    if assistance:
        # Full versions of the same bounded display examples, for accessible disclosure.
        for index in range(len(source.headers)):
            samples = (
                row.values[index]
                for row in source.rows[:PROFILE_SCAN_ROWS]
                if index not in row.formulas and row.values[index].strip()
            )
            mapping_samples.append(distinct_samples(samples))
    if assistance:
        format_columns = list(assistance.format_columns)
    format_columns = show_source_format_issues(format_columns, source_format_issues)
    for issue in source_format_issues:
        message = mapping_issue_message(issue)
        if not message:
            continue
        step_mapping_errors.append(message)
        if issue.column_index is not None:
            mapping_errors[issue.column_index] = message
    if step_mapping_errors:
        error = merge_error_messages(error, *step_mapping_errors)
    wizard_status = import_wizard_status(state, source, step)
    preview_fields = [field for field in fields if preview and field.key in preview.mapping]
    preview_rows = preview.sample if preview and preview.valid else []
    report_rows = []
    if preview and preview.errors:
        rows = {row.number: row for row in preview.source.rows}
        for number, errors in preview.row_error_groups:
            row = rows[number]
            identifier_values = [
                row.values[preview.mapping.index(key)] if key in preview.mapping else None
                for key in required_keys
                if key in identifier_keys
            ]
            details = []
            for item in errors:
                value = None
                if item.header in preview.source.headers:
                    index = preview.source.headers.index(item.header)
                    value = "[Formula]" if index in row.formulas else row.values[index]
                details.append(dict(field=item.field, value=value, message=item.message))
            report_rows.append(
                dict(number=number, identifier=record_label(identifier_values), errors=details)
            )
    return render_authenticated_template(
        "pages/datasets/import/wizard.html",
        mode=mode,
        difference_page=parse_page_parameter(),
        difference_search=request.args.get("q", ""),
        difference_statuses=request.args.getlist("status"),
        dataset=dataset,
        state=state,
        step=step,
        steps=wizard_status.steps,
        source=source,
        assistance=assistance,
        preview=preview,
        fields=fields,
        mapping_samples=mapping_samples,
        format_columns=format_columns,
        mapping_errors=mapping_errors,
        required_keys=required_keys,
        identifier_keys=identifier_keys,
        preview_fields=preview_fields,
        unmapped_fields=(
            tuple(field for field in fields if field.key not in preview.mapping)
            if preview and mode == "new_version"
            else ()
        ),
        preview_rows=preview_rows,
        report_rows=report_rows,
        success=wizard_status.success,
        automatic=wizard_status.automatic,
        back_step=wizard_status.back_step,
        error=error,
        current_csrf=login_session.csrf_token,
        mapping_reason_text=(
            "Map the source identifier to match existing rows; target columns are optional."
            if mode == "mapping_update"
            else "Map every required and identifier field. Optional fields may be left unmapped."
        ),
    ), status
