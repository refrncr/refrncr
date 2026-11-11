"""Hierarchy tree and table rendering."""

from __future__ import annotations

from flask import abort, redirect, request, url_for

from refrncr.hierarchy import canonical_hierarchy_view
from refrncr.record_search import matches, search_term
from refrncr.web.hierarchies import authorization, render_authenticated_template
from refrncr.web.viewmodels.hierarchies import hierarchy_presentation, sort_hierarchy_rows


def render_hierarchy(view, actor_id):
    selected = request.args.getlist("view") or ["tree"]
    if len(selected) != 1 or selected[0] not in {
        "tree",
        "table",
        "hierarchy",
        "records",
        "hybrid",
    }:
        abort(400, description="Choose Tree or Table.")
    legacy_view = selected[0]
    selected[0] = canonical_hierarchy_view(selected[0])
    records_view = request.args.getlist("records_view") or ["reference"]
    if len(records_view) != 1 or records_view[0] not in {"reference", "flattened"}:
        abort(400, description="Choose a valid hierarchy view.")
    searches = request.args.getlist("q")
    sorts = request.args.getlist("sort") or [""]
    directions = request.args.getlist("direction") or ["asc"]
    if len(searches) > 1 or len(sorts) != 1 or len(directions) != 1:
        abort(400, description="Unsupported hierarchy view parameters.")
    if sorts[0] not in {"", "name", "parent", "key"} or directions[0] not in {"asc", "desc"}:
        abort(400, description="Unsupported hierarchy sort.")
    if set(request.args) - {"view", "records_view", "q", "sort", "direction"}:
        abort(400, description="Unsupported hierarchy view parameters.")
    if legacy_view != selected[0]:
        target = url_for(
            "records.dataset_records_view",
            key=view.context.dataset.key,
            version_id=view.state.version.id if view.state else None,
            concept=True if view.state is None else None,
            view=selected[0],
            q=searches[0] if searches else None,
        )
        return redirect(target, code=301)
    hierarchy_search = search_term(searches[0] if searches else "")
    normalized_search = hierarchy_search.casefold()
    rows, children = hierarchy_presentation(view)
    leaf_rows = [row for row in rows if row["leaf"] and row["node"].parent_id is not None]
    parent_level_count = max((len(row["ancestors"]) for row in leaf_rows), default=0)
    flat_value_columns = []
    for field in view.fields:
        if field.hierarchy_scope == "all_nodes":
            flat_value_columns.extend(
                {"field": field, "label": f"{field.name} Level {level + 1}", "level": level}
                for level in range(parent_level_count)
            )
        flat_value_columns.append({"field": field, "label": f"{field.name} Leaf", "level": None})

    def row_matches(row):
        return matches(row["node"], view.fields, normalized_search) or (
            normalized_search in row["node"].name.casefold()
        )

    if selected[0] == "table":
        unfiltered_rows = rows
        display_rows = [row for row in unfiltered_rows if not normalized_search or row_matches(row)]
    else:
        unfiltered_rows = rows
        if normalized_search:
            matched_rows = [row for row in rows if row_matches(row)]
            visible_ids = {
                candidate["node"].id
                for row in matched_rows
                for candidate in (*row["ancestor_rows"], row)
            }
            display_rows = [row for row in rows if row["node"].id in visible_ids]
        else:
            display_rows = rows

    key_field_key = (getattr(view.identifier, "field_keys", ()) or (None,))[0]
    key_field = next((field for field in view.fields if field.key == key_field_key), None)
    sort_key = sorts[0]
    if selected[0] == "table" and sort_key:
        display_rows = sort_hierarchy_rows(
            display_rows,
            sort_key,
            key_field,
            descending=directions[0] == "desc",
        )

    hierarchy_clear_url = url_for(
        "records.dataset_records_view",
        key=view.context.dataset.key,
        version_id=view.state.version.id if view.state else None,
        concept=True if view.state is None else None,
        view=selected[0],
        sort=sorts[0] or None,
        direction=directions[0] if sorts[0] else None,
    )
    return render_authenticated_template(
        "pages/datasets/hierarchy.html",
        dataset=view.context.dataset,
        context=view.context,
        fields=view.fields,
        identifier=view.identifier,
        hierarchy_rows=rows,
        hierarchy_errors=view.hierarchy_errors,
        leaf_rows=leaf_rows,
        parent_level_count=parent_level_count,
        flat_value_columns=flat_value_columns,
        records_view=records_view[0],
        display_rows=display_rows,
        hierarchy_result_total=len(unfiltered_rows),
        leaf_count=len(leaf_rows),
        hierarchy_search=hierarchy_search,
        hierarchy_clear_url=hierarchy_clear_url,
        selected_view=selected[0],
        sort_key=sort_key,
        sort_direction=directions[0],
        key_field_key=key_field_key,
        default_column_keys=(
            [key_field_key]
            + [field.key for field in view.fields if field.key != key_field_key][
                : 6 - bool(key_field_key)
            ]
            if key_field_key
            else [field.key for field in view.fields[:6]]
        ),
        version_state=view.state,
        may_cancel_scheduled=view.may_maintain,
        may_export=view.may_export,
        may_import=view.may_maintain,
        may_edit=view.state is None and view.may_maintain,
        may_view_access=view.may_maintain,
        may_change_owner=authorization.datasets.can_change_dataset_owner(
            actor_id, view.context.dataset.id
        ),
        draft_in_progress=view.concept is not None,
        may_start_concept=(
            view.may_maintain
            and view.concept is None
            and view.state is not None
            and view.state.status == "Current"
        ),
    )
