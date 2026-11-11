"""Dataset discovery, creation, details, and concept routes."""

from types import SimpleNamespace

from flask import abort, g, redirect, render_template, request, url_for

from refrncr.change_status import ChangeStatus
from refrncr.presentation_metadata import DATASET_ICONS, key_from_name, rendered_dataset_icon
from refrncr.services import ConflictError, NotFoundError, ValidationError, parse_expected_revision
from refrncr.version_contracts import mapping_incomplete_message
from refrncr.web.datasets import (
    active_navigation,
    authorization,
    blueprint,
    dataset_versions,
    datasets,
    render_authenticated_template,
    render_mapping_template,
    require_authenticated,
    require_csrf,
    require_dataset_dependencies,
)
from refrncr.web.routing import version_errors


@blueprint.dataset_route("/datasets", capability="can_access_community", methods=["GET"])
def dataset_list():
    actor, _login_session = require_authenticated()
    _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
        require_dataset_dependencies()
    )
    dataset_kind = request.args.get("kind")
    if dataset_kind not in {None, "reference", "mapping", "hierarchy"}:
        abort(404)
    visible_datasets = dataset_service.list_dataset_overview(actor.id)
    if dataset_kind is not None:
        visible_datasets = tuple(
            context for context in visible_datasets if context.dataset.kind == dataset_kind
        )
    page_title = {
        None: "Datasets",
        "reference": "Reference datasets",
        "mapping": "Mapping datasets",
        "hierarchy": "Hierarchy datasets",
    }[dataset_kind]
    return render_mapping_template(
        "pages/datasets/list.html",
        actor=actor,
        may_create=_authorization.datasets.can_create_dataset(actor.id),
        datasets=visible_datasets,
        deletion_notice=request.args.getlist("deleted") == ["1"],
        page_title=page_title,
        empty_dataset_label=page_title.lower(),
        dataset_kind=dataset_kind,
        current_actor=actor,
        current_csrf=_login_session.csrf_token,
        active_navigation=(
            f"{dataset_kind}_datasets" if dataset_kind is not None else active_navigation()
        ),
        dataset_ui_enabled=True,
    )


def render_dataset_create_form(
    *,
    key: str = "",
    name: str = "",
    description: str = "",
    initial_kind: str | None = None,
    error=None,
    error_field=None,
):
    actor, login_session = require_authenticated()
    _auth, service, field_service, *_rest = require_dataset_dependencies()
    eligible_teams = _auth.datasets.creation_teams(actor.id)
    available_reference_tables, reference_table_fields = service.mapping_source_choices(actor.id)
    return render_template(
        "pages/datasets/create.html",
        dataset_icon_choices=DATASET_ICONS,
        selected_icon=request.form.get("icon", ""),
        teams=eligible_teams,
        owner_team_id=request.form.get(
            "owner_team_id", eligible_teams[0].id if len(eligible_teams) == 1 else ""
        ),
        key=key or (key_from_name(name) if not error else ""),
        name=name,
        description=description,
        table_kind=request.form.get("kind") or initial_kind or "reference",
        mapping_mode="linked",
        source_system=request.form.get("source_system", ""),
        target_system=request.form.get("target_system", ""),
        source_dataset_id=request.form.get("source_dataset_id", ""),
        target_dataset_id=request.form.get("target_dataset_id", ""),
        source_field_key=request.form.get("source_field_key", ""),
        target_field_key=request.form.get("target_field_key", ""),
        available_reference_tables=available_reference_tables,
        reference_table_fields=reference_table_fields,
        error=error,
        error_field=error_field,
        current_actor=actor,
        current_csrf=login_session.csrf_token,
        active_navigation=active_navigation(),
        dataset_ui_enabled=True,
    )


@blueprint.dataset_route("/datasets/new", capability="can_create_dataset", methods=["GET"])
def new_dataset():
    require_dataset_dependencies()
    initial_kind = request.args.get("kind")
    if initial_kind not in {None, "reference", "mapping", "hierarchy"}:
        abort(404)
    return render_dataset_create_form(initial_kind=initial_kind)


@blueprint.dataset_route("/datasets", capability="can_create_dataset", methods=["POST"])
def create_dataset():
    actor, login_session = require_authenticated()
    _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
        require_dataset_dependencies()
    )
    require_csrf(login_session.csrf_token)
    key = request.form.get("key", "")
    name = request.form.get("dataset_label", request.form.get("name", ""))
    description = request.form.get("description", "")
    try:
        submitted_teams = request.form.getlist("owner_team_id")
        if len(submitted_teams) != 1 or not submitted_teams[0]:
            raise ValidationError("Choose an owning team.")
        dataset = dataset_service.create_dataset(
            actor.id,
            key,
            name,
            description,
            submitted_teams[0],
            icon=request.form.get("icon", ""),
            kind=request.form.get("kind", "reference"),
            mapping_mode=request.form.get("mapping_mode") or None,
            source_system=request.form.get("source_system") or None,
            target_system=request.form.get("target_system") or None,
            source_dataset_id=request.form.get("source_dataset_id") or None,
            target_dataset_id=request.form.get("target_dataset_id") or None,
            source_field_key=request.form.get("source_field_key") or None,
            target_field_key=request.form.get("target_field_key") or None,
        )
    except (ValidationError, ConflictError) as error:
        message = str(error)
        error_field = error.field
        response = render_dataset_create_form(
            key=key,
            name=name,
            description=description,
            error=message,
            error_field=error_field,
        )
        return response, 409 if isinstance(error, ConflictError) else 400
    return redirect(
        url_for(
            "datasets.dataset_detail",
            key=dataset.key,
            concept=True,
            tab="fields",
            _anchor="fields",
        )
    )


@blueprint.dataset_route(
    "/datasets/<key>/concept",
    capability="can_view_dataset",
    methods=["GET"],
    defaults={"concept": True},
)
@blueprint.dataset_route(
    "/datasets/<key>",
    capability="can_view_dataset",
    methods=["GET"],
    defaults={"concept": False},
)
@blueprint.dataset_route(
    "/datasets/<key>/versions/<version_id>",
    capability="can_view_dataset",
    methods=["GET"],
    defaults={"concept": False},
)
@version_errors
def dataset_detail(key: str, version_id=None, concept=False):
    edit_mode = request.args.get("edit")
    if edit_mode == "1":
        edit_mode = "all"
    if edit_mode not in (None, "general", "ownership", "all"):
        abort(404)
    return render_dataset_configuration(
        key, version_id=version_id, concept=concept, edit_mode=edit_mode
    )


def dataset_settings_redirect(key, *, edit_mode=None):
    actor, _login_session = require_authenticated()
    try:
        view = dataset_versions.read(actor.id, key, page=None, prefer_concept=True)
    except NotFoundError:
        try:
            datasets.get_dataset_configuration(actor.id, key)
        except NotFoundError:
            abort(404)
        version_id = None
        use_concept = False
    else:
        use_concept = view.state is None
        # Settings describe dataset-wide metadata. Do not send a legacy
        # configure link to a scheduled/expired snapshot: that would expose
        # version context (and its release details) instead of Settings.
        version_id = (
            view.state.version.id
            if view.state and view.state.status == "Current" and not use_concept
            else None
        )
        if view.state and view.state.status != "Current":
            use_concept = False
    return redirect(
        url_for(
            "datasets.dataset_detail",
            key=key,
            version_id=version_id,
            concept=True if use_concept else None,
            edit=edit_mode,
            tab="dataset-details",
            _anchor="dataset-details",
        )
    )


@blueprint.dataset_route(
    "/datasets/<key>/configure",
    capability="can_view_dataset",
    methods=["GET"],
)
@version_errors
def dataset_configuration(key):
    edit_mode = request.args.get("edit")
    if edit_mode == "1":
        edit_mode = "all"
    if edit_mode not in (None, "general", "ownership", "all"):
        abort(404)
    return dataset_settings_redirect(key, edit_mode=edit_mode)


def render_dataset_configuration(
    key,
    *,
    version_id=None,
    concept=False,
    edit_mode=False,
    name=None,
    description=None,
    error=None,
    error_field=None,
    access_error=None,
    access_subject="",
):
    actor, login_session = require_authenticated()
    require_dataset_dependencies()
    field_context_label = None
    draft_in_progress = False
    may_edit_key = False
    mapping_configuration = None
    try:
        view = dataset_versions.read(actor.id, key, version_id, page=None, prefer_concept=concept)
        if (
            version_id is None
            and not concept
            and view.state is not None
            and view.state.status != "Current"
        ):
            # The default version lookup may select Scheduled or Expired
            # when no Current version exists. Settings are dataset-wide;
            # show metadata, not a non-current version's release details.
            raise NotFoundError("No Current version")
    except NotFoundError:
        if version_id is not None or concept:
            raise
        configuration = datasets.get_dataset_configuration(actor.id, key)
        dataset = configuration.dataset
        context = SimpleNamespace(record_count=0, last_updated=dataset.updated_at)
        state, identifier = None, None
        fields, field_states = (), {}
        editable = configuration.may_maintain
        may_change_owner = configuration.may_govern
        may_edit_key = configuration.may_edit_key
        may_export = authorization.datasets.can_export_dataset(actor.id, dataset.id)
        schema_admin, may_start_concept = False, False
        field_context_label = "No Current version or active Draft available."
        if dataset.kind == "mapping":
            mapping_configuration = dataset
    else:
        dataset = view.context.dataset
        if dataset.kind == "mapping":
            # PublishedDataset intentionally contains snapshot content, not mutable
            # mapping configuration. Read the separately authorized current metadata.
            mapping_configuration = datasets.get_dataset_configuration(actor.id, key).dataset
        editable = view.may_maintain
        schema_admin = view.state is None and view.may_maintain
        may_change_owner = editable and authorization.datasets.can_change_dataset_owner(
            actor.id, dataset.id
        )
        context, state, identifier = view.context, view.state, view.identifier
        fields = view.fields
        field_states = {
            change.id: change.status
            for change in (g.concept_ui.field_changes if concept else ())
            if change.status != ChangeStatus.ADDED
        }
        may_start_concept = (
            view.may_maintain
            and view.concept is None
            and view.state is not None
            and view.state.status == "Current"
        )
        if editable:
            may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
        may_export = view.may_export
        if view.state is not None:
            field_context_label = (
                f"{view.state.status} · Version {view.state.version.number} · Read only"
            )
        draft_in_progress = view.concept is not None
    mapping_source_table = None
    if mapping_configuration and mapping_configuration.source_dataset_id:
        try:
            visible_dataset_lookup = datasets.get_visible_dataset_by_id
            mapping_source_table = visible_dataset_lookup(
                actor.id, mapping_configuration.source_dataset_id
            )
        except NotFoundError:
            mapping_source_table = None
    mapping_incomplete_text = (
        mapping_incomplete_message(fields) if dataset.kind == "mapping" else None
    )
    if edit_mode and not editable:
        abort(404)
    if edit_mode == "ownership" and not may_change_owner:
        abort(404)
    access_teams, access_users, access_entries = [], [], []
    access_grant_teams, access_grant_users = [], []
    may_view_access = editable
    may_manage_export = False
    export_policy = None
    if may_view_access:
        _, access_teams, access_users, access_entries, export_policy, may_manage_export = (
            datasets.export_access_context(actor.id, key)
        )
        granted_subjects = {(entry.subject_type, entry.subject_id) for entry, _ in access_entries}
        access_grant_teams = [
            team
            for team in access_teams
            if team.id != dataset.owner_team_id and ("team", team.id) not in granted_subjects
        ]
        access_grant_users = [
            user for user in access_users if ("user", user.id) not in granted_subjects
        ]
    return render_mapping_template(
        "pages/datasets/detail.html",
        dataset=dataset,
        concept=concept,
        mapping_configuration=mapping_configuration,
        source_context_available=identifier is not None,
        context=context,
        settings_return_context=("version" if version_id else "concept" if concept else "detail"),
        settings_return_version_id=version_id,
        configuration_url=(
            url_for(
                "datasets.dataset_detail",
                key=dataset.key,
                version_id=version_id,
                concept=True if concept else None,
                tab="dataset-details",
                _anchor="dataset-details",
            )
        ),
        editable=editable,
        edit_mode=edit_mode,
        name=dataset.name if name is None else name,
        technical_key=request.form.get("technical_key", dataset.key),
        may_edit_key=may_edit_key,
        description=dataset.description if description is None else description,
        error=error,
        error_field=error_field,
        dataset_icon_choices=DATASET_ICONS,
        selected_icon=request.form.get("icon", dataset.icon),
        teams=access_teams,
        selected_team_id=request.form.get("owner_team_id", dataset.owner_team_id),
        expected_owner_team_id=request.form.get("expected_owner_team_id", dataset.owner_team_id),
        schema_admin=schema_admin,
        may_start_concept=may_start_concept,
        may_change_owner=may_change_owner,
        may_view_access=may_view_access,
        may_manage_export=may_manage_export,
        export_policy=export_policy,
        may_export=may_export,
        draft_in_progress=draft_in_progress,
        access_teams=access_teams,
        access_users=access_users,
        access_grant_teams=access_grant_teams,
        access_grant_users=access_grant_users,
        access_entries=access_entries,
        access_error=access_error,
        access_subject=access_subject,
        fields=fields,
        mapping_source_table=mapping_source_table,
        mapping_incomplete=bool(mapping_incomplete_text),
        mapping_incomplete_message=mapping_incomplete_text,
        field_states=field_states,
        field_context_label=field_context_label,
        dataset_icon_label=DATASET_ICONS.get(
            rendered_dataset_icon(dataset.icon, dataset.kind),
            rendered_dataset_icon(dataset.icon, dataset.kind).replace("-", " ").capitalize(),
        ),
        business_key=identifier,
        version_state=state,
    )


@blueprint.dataset_route(
    "/datasets/<key>/source/update",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@version_errors
def dataset_mapping_source_update(key):
    from refrncr.mapping_updates import SourceUpdateBlocked

    actor, session = require_authenticated()
    require_csrf(session.csrf_token)
    dataset = datasets.get_dataset_for_edit(actor.id, key)
    try:
        revisions = request.form.getlist("expected_revision")
        if len(revisions) != 1:
            raise ValidationError("Reload the mapping before updating its source.")
        dataset_versions.update_mapping_source(
            actor.id, dataset.id, parse_expected_revision(revisions[0])
        )
    except SourceUpdateBlocked:
        # Re-render the mapping's shared notice, including newly incompatible sources.
        return redirect(url_for("records.dataset_records_view", key=key), code=303)
    except (ValidationError, ConflictError) as failure:
        status = 409 if isinstance(failure, ConflictError) else 400
        return render_authenticated_template(
            "pages/error.html", error_code=status, error_message=str(failure)
        ), status
    return redirect(
        url_for("records.dataset_records_view", key=key, concept=True, notice="source-updated"),
        code=303,
    )
