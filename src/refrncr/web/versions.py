"""Dataset concept review, publication, and version-history blueprint."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from flask import Flask, abort, current_app, redirect, request, url_for
from werkzeug.local import LocalProxy

from refrncr.change_status import ChangeStatus, change_status
from refrncr.dataset_diff import STALE_MESSAGE, DatasetDiffService
from refrncr.dataset_versions import DatasetVersionService
from refrncr.diff_contracts import STATUSES, DiffCriteria
from refrncr.record_import import DatasetRecordImportService
from refrncr.review_tokens import decode_discard, decode_review, encode_discard, encode_review
from refrncr.services import (
    ConflictError,
    DatasetService,
    NotFoundError,
    ValidationError,
    parse_expected_revision,
)
from refrncr.web.routing import DatasetBlueprint, concept_route, version_errors

blueprint = DatasetBlueprint("versions", __name__)


@dataclass(frozen=True)
class VersionsContext:
    datasets: DatasetService
    dataset_versions: DatasetVersionService
    dataset_diff: DatasetDiffService
    record_imports: DatasetRecordImportService
    review_secret: bytes
    require_authenticated: Callable[[], Any]
    require_csrf: Callable[[str | None], None]
    render_authenticated_template: Callable[..., Any]
    render_mapping_template: Callable[..., Any]
    audit_security: Callable[..., None]


def _context() -> VersionsContext:
    return current_app.extensions["refrncr.versions"]


datasets = LocalProxy(lambda: _context().datasets)
dataset_versions = LocalProxy(lambda: _context().dataset_versions)
dataset_diff = LocalProxy(lambda: _context().dataset_diff)
record_imports = LocalProxy(lambda: _context().record_imports)


def review_secret() -> bytes:
    return _context().review_secret


def require_authenticated():
    return _context().require_authenticated()


def require_csrf(expected_token: str | None) -> None:
    _context().require_csrf(expected_token)


def render_authenticated_template(template_name: str, **context):
    return _context().render_authenticated_template(template_name, **context)


def render_mapping_template(template_name: str, **context):
    return _context().render_mapping_template(template_name, **context)


def audit_security(action: str, *, actor_user_id: str | None = None) -> None:
    _context().audit_security(action, actor_user_id=actor_user_id)


@blueprint.dataset_route(
    "/datasets/<key>/versions/<version_id>/fields/<field_key>",
    capability="can_view_dataset",
    methods=["GET"],
)
@version_errors
def dataset_version_field_detail(key, version_id, field_key):
    actor, _login_session = require_authenticated()
    view = dataset_versions.read(actor.id, key, version_id, page=None)
    field = next((item for item in view.fields if item.key == field_key), None)
    if field is None:
        abort(404)
    return render_authenticated_template(
        "pages/datasets/fields/read.html",
        dataset=view.context.dataset,
        field=field,
        version_state=view.state,
    )


@blueprint.dataset_route(
    "/datasets/<key>/versions",
    capability="can_view_dataset",
    methods=["GET"],
)
@version_errors
def dataset_versions_view(key):
    actor, _session = require_authenticated()
    history = dataset_versions.history(actor.id, key)
    notice = {
        "published": "Dataset version published.",
        "cancelled": "Scheduled version cancelled.",
    }.get(request.args.get("notice"))
    return render_mapping_template(
        "pages/datasets/versions/list.html",
        history=history,
        dataset=history.context.dataset,
        context=history.context,
        notice=notice,
        review_available=bool(history.concept_summary and history.concept_summary.can_review),
    )


def review_error(key, message=STALE_MESSAGE, status=409):
    return render_authenticated_template(
        "pages/datasets/versions/review_error.html", key=key, error=message
    ), status


def review_identity(actor, session, dataset):
    tokens = request.args.getlist("review")
    if len(tokens) != 1:
        audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
        raise NotFoundError("Review not found")
    try:
        identity = decode_review(tokens[0], session.id, review_secret())
    except ValueError:
        audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
        raise ConflictError("This review link is no longer valid. Reload the review.") from None
    if identity.dataset_id != dataset.id or identity.actor_id != actor.id:
        audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
        raise NotFoundError("Review not found")
    return identity, tokens[0]


def review_parameters(*, detail=False):
    allowed = {"review", "section", "q", "status", "anchor", "backwards"}
    if detail:
        allowed.add("detail_page")
    if (
        set(request.args) - allowed
        or len(request.query_string) > 4096
        or any(len(request.args.getlist(name)) != 1 for name in request.args if name != "status")
    ):
        raise ValidationError("Choose valid review parameters.")
    section = request.args.get("section", "Summary")
    if section not in {"Summary", "Fields", "Records"} or request.args.get(
        "backwards", "0"
    ) not in {"0", "1"}:
        raise ValidationError("Choose valid review parameters.")
    try:
        statuses = tuple(change_status(value) for value in request.args.getlist("status"))
    except ValueError:
        raise ValidationError("Choose valid review parameters.") from None
    criteria = DiffCriteria(
        statuses or STATUSES,
        request.args.get("q", ""),
        request.args.get("anchor", ""),
        request.args.get("backwards", "0") == "1",
    )
    # Service revalidates every supplied criterion for trusted programmatic callers.
    dataset_diff._criteria(criteria)
    return section, criteria


def review_url(key, token, section="Summary", criteria=None, **changes):
    criteria = criteria or DiffCriteria()
    values = dict(
        review=token,
        section=section,
        q=criteria.search,
        status=None if set(criteria.statuses) == set(STATUSES) else list(criteria.statuses),
        anchor=criteria.anchor,
        backwards="1" if criteria.backwards else "0",
    )
    values.update(changes)
    return url_for("versions.dataset_version_review", key=key, **values)


@blueprint.dataset_route(
    "/datasets/<key>/versions/review",
    capability="can_view_dataset",
    methods=["GET"],
)
@version_errors
@concept_route
def dataset_version_review(key):
    actor, session = require_authenticated()
    dataset = datasets.get_dataset_for_edit(actor.id, key)
    try:
        if not request.args:
            opened = dataset_diff.open_review(actor.id, dataset.id)
            token = encode_review(opened.identity, session.id, review_secret())
            return redirect(review_url(key, token), code=303)
        identity, token = review_identity(actor, session, dataset)
        section, criteria = review_parameters()
        result = dataset_diff.review(actor.id, identity, section, criteria)
        selected_statuses = tuple(
            dict.fromkeys(change_status(value) for value in request.args.getlist("status"))
        )
        field_items = result.items if section == "Fields" else ()
        if section == "Fields":
            field_items = tuple(
                replace(
                    item,
                    properties=tuple(
                        change
                        for change in item.properties
                        if not (
                            (
                                dataset.kind != "mapping"
                                and change.name in {"Mapping role", "Source field"}
                            )
                            or (dataset.kind != "hierarchy" and change.name == "Applicability")
                            or (
                                item.status == ChangeStatus.ADDED
                                and change.before is None
                                and change.after is None
                            )
                        )
                    ),
                )
                for item in result.items
            )
        return render_mapping_template(
            "pages/datasets/versions/review.html",
            dataset=dataset,
            identity=identity,
            token=token,
            section=section,
            criteria=criteria,
            result=result,
            field_items=field_items,
            selected_statuses=selected_statuses,
            sections={
                name: review_url(key, token, name) for name in ("Summary", "Fields", "Records")
            },
            page_url=lambda **values: review_url(key, token, section, criteria, **values),
            detail_url=lambda kind, object_id: url_for(
                "versions.dataset_version_record_diff"
                if kind == "record"
                else "versions.dataset_version_field_diff",
                key=key,
                object_id=object_id,
                review=token,
                section=section,
                q=criteria.search,
                status=list(selected_statuses),
                anchor=criteria.anchor,
                backwards="1" if criteria.backwards else "0",
            ),
        )
    except ConflictError as error:
        return review_error(key, str(error))
    except ValidationError as error:
        return review_error(key, str(error), 400)


def render_diff_detail(key, object_id, kind):
    actor, session = require_authenticated()
    dataset = datasets.get_dataset_for_edit(actor.id, key)
    try:
        identity, token = review_identity(actor, session, dataset)
        section, criteria = review_parameters(detail=True)
        if kind == "record":
            raw_page = request.args.get("detail_page", "1")
            if len(raw_page) > 10 or not raw_page.isascii() or not raw_page.isdigit():
                raise ValidationError("Choose a valid detail page.")
            item = dataset_diff.record_detail(actor.id, identity, object_id, int(raw_page))
        else:
            item = dataset_diff.field_detail(actor.id, identity, object_id)
        return render_authenticated_template(
            "pages/datasets/versions/diff_detail.html",
            dataset=dataset,
            identity=identity,
            kind=kind,
            item=item,
            return_url=review_url(key, token, section, criteria),
            detail_page_url=lambda number: url_for(
                "versions.dataset_version_record_diff",
                key=key,
                object_id=object_id,
                review=token,
                section=section,
                q=criteria.search,
                status=(
                    None if set(criteria.statuses) == set(STATUSES) else list(criteria.statuses)
                ),
                anchor=criteria.anchor,
                backwards="1" if criteria.backwards else "0",
                detail_page=number,
            ),
        )
    except ConflictError as error:
        return review_error(key, str(error))
    except ValidationError as error:
        return review_error(key, str(error), 400)


@blueprint.dataset_route(
    "/datasets/<key>/versions/review/records/<object_id>",
    capability="can_view_dataset",
    methods=["GET"],
)
@version_errors
@concept_route
def dataset_version_record_diff(key, object_id):
    return render_diff_detail(key, object_id, "record")


@blueprint.dataset_route(
    "/datasets/<key>/versions/review/fields/<object_id>",
    capability="can_view_dataset",
    methods=["GET"],
)
@version_errors
@concept_route
def dataset_version_field_diff(key, object_id):
    return render_diff_detail(key, object_id, "field")


@blueprint.dataset_route(
    "/datasets/<key>/versions/publish",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@version_errors
@concept_route
def dataset_version_publish(key):
    actor, session = require_authenticated()
    dataset = datasets.get_dataset_for_edit(actor.id, key)
    expected_review, review_token = None, None
    if request.method == "POST":
        require_csrf(session.csrf_token)
    if "review" in request.args:
        try:
            expected_review, review_token = review_identity(actor, session, dataset)
            dataset_diff.review(actor.id, expected_review)
        except ConflictError as failure:
            if request.method == "POST":
                return redirect(review_url(key, request.args["review"]), code=303)
            return review_error(key, str(failure))
    error, status = None, 200
    effective = request.form.get("effective_date", "")[:10]
    note = request.form.get("release_note", "")[:2000]
    if request.method == "POST":
        if request.form.getlist("dataset_id") != [dataset.id]:
            raise NotFoundError("Dataset not found")
        try:
            if request.form.getlist("confirmed") != ["yes"]:
                raise ValidationError("Confirm that you reviewed this version before publishing.")
            dates = request.form.getlist("effective_date")
            if len(dates) != 1 or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", dates[0]) is None:
                raise ValidationError("Choose an effective calendar date.")
            try:
                effective_date = date.fromisoformat(dates[0])
            except ValueError:
                raise ValidationError("Choose a valid effective calendar date.") from None
            revisions = request.form.getlist("expected_revision")
            if len(revisions) != 1 or len(request.form.getlist("release_note")) != 1:
                raise ValidationError("Review the publication form again.")
            expected = parse_expected_revision(revisions[0])
            dataset_versions.publish(
                actor.id,
                dataset.id,
                effective_date,
                request.form["release_note"],
                expected,
                expected_review=expected_review,
            )
            return redirect(
                url_for("versions.dataset_versions_view", key=key, notice="published"), code=303
            )
        except (ValidationError, ConflictError) as failure:
            if expected_review is not None and isinstance(failure, ConflictError):
                return redirect(review_url(key, review_token), code=303)
            error, status = str(failure), 409 if isinstance(failure, ConflictError) else 400
    review = None
    try:
        review = dataset_versions.review(actor.id, dataset.id)
        if expected_review is not None and review[0] != expected_review.revision:
            return review_error(key)
    except (ValidationError, ConflictError) as failure:
        error, status = error or str(failure), 409
    today = dataset_versions.clock.today().isoformat()
    mapping_source = datasets.mapping_source(actor.id, key) if dataset.kind == "mapping" else None
    minimum_date = (
        max(dataset_versions.clock.today(), mapping_source["effective_date"])
        if mapping_source
        else dataset_versions.clock.today()
    )
    concept_summary = dataset_versions.history(actor.id, key).concept_summary
    return render_authenticated_template(
        "pages/datasets/versions/publish.html",
        dataset=dataset,
        review=review,
        error=error,
        effective_date=effective or minimum_date.isoformat(),
        minimum_date=minimum_date,
        mapping_source=mapping_source,
        mapping_usage=datasets.mapping_usage(actor.id, key),
        release_note=note,
        today=today,
        review_token=review_token,
        review_available=bool(review_token) or bool(concept_summary and concept_summary.can_review),
    ), status


@blueprint.dataset_route(
    "/datasets/<key>/versions/<version_id>/cancel",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@version_errors
def dataset_version_cancel(key, version_id):
    actor, session = require_authenticated()
    dataset = datasets.get_dataset_for_edit(actor.id, key)
    error, status = None, 200
    if request.method == "POST":
        require_csrf(session.csrf_token)
        try:
            if request.form.getlist("confirmed") != ["yes"]:
                raise ValidationError("Confirm cancellation of this Scheduled version.")
            dataset_versions.cancel(actor.id, dataset.id, version_id)
            return redirect(
                url_for("versions.dataset_versions_view", key=key, notice="cancelled"), code=303
            )
        except (ValidationError, ConflictError) as failure:
            error, status = str(failure), 409 if isinstance(failure, ConflictError) else 400
    history = dataset_versions.history(actor.id, key)
    selected = next((state for state in history.entries if state.version.id == version_id), None)
    if selected is None:
        raise NotFoundError("Dataset version not found")
    if selected.status != "Scheduled":
        error, status = (
            "Only a Scheduled version can be cancelled before its effective date.",
            409,
        )
    return render_authenticated_template(
        "pages/datasets/versions/cancel.html",
        dataset=dataset,
        state=selected,
        error=error,
        can_cancel=selected.status == "Scheduled",
    ), status


@blueprint.dataset_route(
    "/datasets/<key>/concept/new",
    capability="can_maintain_dataset",
    methods=["POST"],
)
@version_errors
def dataset_concept_create(key):
    actor, session = require_authenticated()
    require_csrf(session.csrf_token)
    dataset = datasets.get_dataset_for_edit(actor.id, key)
    try:
        dataset_versions.create_concept(actor.id, dataset.id)
    except ConflictError as error:
        return render_authenticated_template(
            "pages/datasets/concept_required.html",
            key=key,
            error=str(error),
            already_active=True,
            has_current=dataset_versions.history(actor.id, key).has_current,
        ), 409
    return redirect(url_for("records.dataset_records_view", key=key, concept=True), code=303)


@blueprint.dataset_route(
    "/datasets/<key>/concept/discard",
    capability="can_maintain_dataset",
    methods=["GET", "POST"],
)
@version_errors
@concept_route
def dataset_concept_discard(key):
    actor, session = require_authenticated()
    dataset = datasets.get_dataset_for_edit(actor.id, key)
    error, status = None, 200
    if request.method == "POST":
        require_csrf(session.csrf_token)
        if set(request.form) != {"csrf_token", "confirmation", "confirmed"} or any(
            len(request.form.getlist(key)) != 1 for key in request.form
        ):
            return "Review the discard form again.", 400
        try:
            identity = decode_discard(request.form["confirmation"], session.id, review_secret())
        except ValueError:
            audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
            raise NotFoundError("Concept not found") from None
        if identity.actor_id != actor.id or identity.dataset_id != dataset.id:
            audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
            raise NotFoundError("Concept not found")
        try:
            if request.form["confirmed"] != "yes":
                raise ValidationError("Confirm discard of this Concept.")
            result = dataset_versions.discard_concept(actor.id, dataset.id, identity)
            record_imports.store.revoke_concept(result.identity.concept_id)
            return redirect(
                url_for("datasets.dataset_detail", key=key, notice="discarded"), code=303
            )
        except (ConflictError, ValidationError) as failure:
            error, status = str(failure), 409 if isinstance(failure, ConflictError) else 400
    review, token = None, None
    try:
        review = dataset_versions.review_discard(actor.id, dataset.id)
        token = encode_discard(review.identity, session.id, review_secret())
    except ConflictError as failure:
        error, status = error or str(failure), 409
    return render_authenticated_template(
        "pages/datasets/concepts/discard.html",
        dataset=dataset,
        review=review,
        confirmation=token,
        error=error,
    ), status


def register_versions_blueprint(
    app: Flask,
    *,
    datasets: DatasetService | None,
    dataset_versions: DatasetVersionService | None,
    dataset_diff: DatasetDiffService | None,
    record_imports: DatasetRecordImportService | None,
    review_secret: bytes,
    require_authenticated: Callable[[], Any],
    require_csrf: Callable[[str | None], None],
    render_authenticated_template: Callable[..., Any],
    render_mapping_template: Callable[..., Any],
    audit_security: Callable[..., None],
) -> None:
    dependencies = (datasets, dataset_versions, dataset_diff, record_imports)
    if all(dependency is not None for dependency in dependencies):
        app.extensions["refrncr.versions"] = VersionsContext(
            datasets=datasets,
            dataset_versions=dataset_versions,
            dataset_diff=dataset_diff,
            record_imports=record_imports,
            review_secret=review_secret,
            require_authenticated=require_authenticated,
            require_csrf=require_csrf,
            render_authenticated_template=render_authenticated_template,
            render_mapping_template=render_mapping_template,
            audit_security=audit_security,
        )
    app.register_blueprint(blueprint)
