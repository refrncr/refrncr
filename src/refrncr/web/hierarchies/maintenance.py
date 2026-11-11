"""Hierarchy node maintenance routes."""

from __future__ import annotations

from flask import abort, redirect, request, url_for

from refrncr.hierarchy import canonical_hierarchy_view
from refrncr.services import ConflictError, RecordValidationError, ValidationError
from refrncr.web.hierarchies import (
    blueprint,
    dataset_versions,
    exact_expected_revision,
    hierarchy,
    render_authenticated_template,
    require_authenticated,
)
from refrncr.web.routing import concept_route, version_errors
from refrncr.web.viewmodels.hierarchies import hierarchy_presentation


def hierarchy_edit_context(key, node_id=None):
    actor, _session = require_authenticated()
    view = dataset_versions.read(actor.id, key, page=None, prefer_concept=True)
    if view.context.dataset.kind != "hierarchy" or view.state or not view.may_maintain:
        abort(404)
    rows, _children = hierarchy_presentation(view)
    row = next((row for row in rows if row["node"].id == node_id), None)
    if node_id is not None and row is None:
        abort(404)
    return actor, view, rows, row


def render_hierarchy_node_form(view, rows, row=None, *, mode="structure", error=None, errors=None):
    node = row["node"] if row else None
    node_ids = {entry["node"].id for entry in rows}
    excluded = {node.id} if node else set()
    if node:
        for _ in rows:
            descendants = {
                entry["node"].id for entry in rows if entry["node"].parent_id in excluded
            }
            if descendants <= excluded:
                break
            excluded.update(descendants)
    applicable = tuple(
        field
        for field in view.fields
        if row and (row["leaf"] or field.hierarchy_scope == "all_nodes")
    )
    parent_id = (
        request.form.get("parent_id", request.args.get("parent_id", node.parent_id if node else ""))
        or None
    )
    if not node and rows and parent_id is None and request.method == "GET":
        parent_id = rows[0]["node"].id
    if parent_id is not None and parent_id not in node_ids:
        abort(404)
    return_view, return_q, return_sort, return_direction = hierarchy_return_view()
    return render_authenticated_template(
        "pages/datasets/hierarchy/node_form.html",
        dataset=view.context.dataset,
        node=node,
        node_row=row,
        mode=mode,
        fields=applicable,
        inactive_fields=tuple(field for field in view.fields if field not in applicable and row),
        parent_options=[entry for entry in rows if entry["node"].id not in excluded],
        parent_id=parent_id,
        name=request.form.get("node_label", request.form.get("name", node.name if node else "")),
        values=(
            {
                key.removeprefix("field:"): value
                for key, value in request.form.items()
                if key.startswith("field:")
            }
            if request.method == "POST"
            else (row["values"] if row else {})
        ),
        raw_values=request.method == "POST",
        expected_revision=request.form.get("expected_revision", view.context.dataset.revision),
        return_view=return_view,
        return_q=return_q,
        return_sort=return_sort,
        return_direction=return_direction,
        error=error,
        errors=errors or {},
    )


def hierarchy_return_view():
    values = request.form if request.method == "POST" else request.args
    selected = values.getlist("return_view") or values.getlist("view") or ["tree"]
    selected[0] = canonical_hierarchy_view(selected[0])
    searches = values.getlist("return_q") or values.getlist("q") or [""]
    sorts = values.getlist("return_sort") or values.getlist("sort") or [""]
    directions = values.getlist("return_direction") or values.getlist("direction") or ["asc"]
    legacy_records = (
        values.getlist("return_records_view") or values.getlist("records_view") or ["reference"]
    )
    if (
        len(selected) != 1
        or selected[0] not in {"tree", "table"}
        or len(searches) != 1
        or len(sorts) != 1
        or sorts[0] not in {"", "name", "parent", "key"}
        or len(directions) != 1
        or directions[0] not in {"asc", "desc"}
        or len(legacy_records) != 1
        or legacy_records[0] not in {"reference", "flattened"}
    ):
        abort(400, description="Choose a valid return view.")
    return selected[0], searches[0], sorts[0], directions[0]


@blueprint.dataset_route(
    "/datasets/<key>/concept/nodes/new",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@version_errors
@concept_route
def dataset_hierarchy_node_create(key):
    actor, view, rows, _row = hierarchy_edit_context(key)
    if request.method == "POST":
        try:
            hierarchy.create_node(
                actor.id,
                key,
                request.form.get("node_label", request.form.get("name", "")),
                request.form.get("parent_id") or None,
                expected_revision=exact_expected_revision(),
            )
        except (ValidationError, ConflictError) as error:
            return render_hierarchy_node_form(view, rows, error=str(error)), 409 if isinstance(
                error, ConflictError
            ) else 400
        return redirect(url_for("records.dataset_records_view", key=key, concept=True))
    return render_hierarchy_node_form(view, rows)


@blueprint.dataset_route(
    "/datasets/<key>/concept/nodes/<node_id>/edit",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@version_errors
@concept_route
def dataset_hierarchy_node_edit(key, node_id):
    actor, view, rows, row = hierarchy_edit_context(key, node_id)
    if request.method == "POST":
        submitted = {}
        if any(
            form_key.startswith("field:") and len(form_values) != 1
            for form_key, form_values in request.form.lists()
        ):
            abort(400, description="Submit exactly one value per field.")
        for form_key, form_values in request.form.lists():
            if form_key.startswith("field:"):
                submitted[form_key.removeprefix("field:")] = form_values[0]
        try:
            hierarchy.update_node_and_values(
                actor.id,
                key,
                node_id,
                request.form.get("node_label", request.form.get("name", "")),
                request.form.get("parent_id") or None,
                submitted,
                expected_revision=exact_expected_revision(),
            )
        except (ValidationError, ConflictError) as error:
            return render_hierarchy_node_form(view, rows, row, error=str(error)), 409 if isinstance(
                error, ConflictError
            ) else 400
        return redirect(
            url_for(
                "records.dataset_records_view",
                key=key,
                concept=True,
                view=hierarchy_return_view()[0],
                q=hierarchy_return_view()[1] or None,
                sort=hierarchy_return_view()[2] or None,
                direction=hierarchy_return_view()[3] if hierarchy_return_view()[2] else None,
            )
        )
    return render_hierarchy_node_form(view, rows, row)


@blueprint.dataset_route(
    "/datasets/<key>/concept/nodes/<node_id>/delete",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@version_errors
@concept_route
def dataset_hierarchy_node_delete(key, node_id):
    actor, view, rows, row = hierarchy_edit_context(key, node_id)
    if row["node"].parent_id is None:
        abort(400, description="The root node cannot be deleted.")
    subtree_count = 1 + sum(
        any(ancestor["node"].id == node_id for ancestor in entry["ancestor_rows"])
        for entry in rows
        if entry["node"].id != node_id
    )
    error, status = None, 200
    if request.method == "POST":
        try:
            if set(request.form) - {"csrf_token", "confirm", "expected_revision"}:
                raise ValidationError("Unknown form field")
            if request.form.getlist("confirm") != ["yes"]:
                raise ValidationError("Explicit confirmation is required")
            hierarchy.delete_subtree(
                actor.id,
                key,
                node_id,
                expected_revision=exact_expected_revision(),
            )
        except (ValidationError, ConflictError) as failure:
            error = str(failure)
            status = 409 if isinstance(failure, ConflictError) else 400
        else:
            return redirect(url_for("records.dataset_records_view", key=key, concept=True))
    response = render_authenticated_template(
        "pages/datasets/hierarchy/delete.html",
        dataset=view.context.dataset,
        node_row=row,
        subtree_count=subtree_count,
        expected_revision=request.form.get("expected_revision", view.context.dataset.revision),
        error=error,
    )
    return (response, status) if status != 200 else response


@blueprint.dataset_route(
    "/datasets/<key>/concept/nodes/<node_id>/values",
    capability="can_view_dataset",
    methods=["GET", "POST"],
)
@version_errors
@concept_route
def dataset_hierarchy_node_values(key, node_id):
    actor, view, rows, row = hierarchy_edit_context(key, node_id)
    return_view, return_q, return_sort, return_direction = hierarchy_return_view()
    if request.method == "POST":
        applicable = [
            field for field in view.fields if row["leaf"] or field.hierarchy_scope == "all_nodes"
        ]
        submitted, errors = {}, {}
        expected_fields = {f"field:{field.key}" for field in applicable}
        for form_key, form_values in request.form.lists():
            if form_key in {
                "csrf_token",
                "expected_revision",
                "return_view",
                "return_q",
                "return_sort",
                "return_direction",
                "return_records_view",
            }:
                continue
            field_key = form_key.removeprefix("field:")
            if form_key not in expected_fields:
                errors[field_key] = "This field does not apply to the selected node."
            elif len(form_values) != 1:
                errors[field_key] = "Submit exactly one value."
            else:
                submitted[field_key] = form_values[0]
        try:
            if errors:
                raise RecordValidationError(errors)
            hierarchy.update_values(
                actor.id,
                key,
                node_id,
                submitted,
                expected_revision=exact_expected_revision(),
            )
        except (ValidationError, ConflictError) as error:
            return render_hierarchy_node_form(
                view,
                rows,
                row,
                mode="values",
                error=str(error),
                errors=getattr(error, "errors", {}),
            ), 409 if isinstance(error, ConflictError) else 400
        return redirect(
            url_for(
                "records.dataset_records_view",
                key=key,
                concept=True,
                view=return_view,
                q=return_q or None,
                sort=return_sort or None,
                direction=return_direction if return_sort else None,
            )
        )
    return render_hierarchy_node_form(view, rows, row, mode="values")
