"""Jinja filters and presentation globals for the Community browser app."""

from datetime import UTC

from flask import Flask
from markupsafe import Markup

from refrncr.audit_history import MAX_AUDIT_DATETIME_LENGTH
from refrncr.change_status import ChangeStatus
from refrncr.datatypes import get_datatype
from refrncr.diff_contracts import MAX_SEARCH
from refrncr.hierarchy import MAX_HIERARCHY_NODE_NAME
from refrncr.presentation import (
    DISPLAY_STATUSES,
    dataset_kind_label,
    pluralize,
    status_badge_variant,
    status_label,
)
from refrncr.presentation_metadata import (
    MAX_DATASET_ICON_LENGTH,
    MAX_FIELD_DEFINITION,
    MAX_FIELD_UNIT,
    dataset_icon_sprite,
    default_dataset_icon,
    rendered_dataset_icon,
)
from refrncr.source_formats import MAX_SOURCE_TIMEZONE_LENGTH

MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def register_template_filters(app: Flask) -> None:
    """Register the shared Jinja filters and globals without changing their order."""
    app.jinja_env.globals["rendered_dataset_icon"] = rendered_dataset_icon
    app.jinja_env.globals["non_login_form_attrs"] = lambda: Markup(
        "autocomplete='off' data-form-type='other' data-1p-ignore "
        "data-lpignore='true' data-bwignore='true'"
    )
    app.jinja_env.globals["non_login_field_attrs"] = lambda: Markup(
        "data-form-type='other' data-1p-ignore data-lpignore='true' data-bwignore='true'"
    )
    app.jinja_env.globals["dataset_icon_sprite"] = dataset_icon_sprite
    app.jinja_env.globals["default_dataset_icon"] = default_dataset_icon
    app.jinja_env.filters["datatype_label"] = lambda value: get_datatype(value).label
    app.jinja_env.filters["dataset_title"] = lambda value: value.replace("->", "→")
    app.jinja_env.filters["status_label"] = status_label
    app.jinja_env.filters["status_badge_variant"] = status_badge_variant
    app.jinja_env.filters["dataset_kind_label"] = dataset_kind_label
    app.jinja_env.filters["pluralize"] = pluralize
    app.jinja_env.globals["dataset_kind_label"] = dataset_kind_label
    app.jinja_env.globals["ChangeStatus"] = ChangeStatus
    app.jinja_env.globals["DISPLAY_STATUSES"] = DISPLAY_STATUSES
    app.jinja_env.globals["status_label"] = status_label
    app.jinja_env.globals.update(
        MAX_AUDIT_DATETIME_LENGTH=MAX_AUDIT_DATETIME_LENGTH,
        MAX_DATASET_ICON_LENGTH=MAX_DATASET_ICON_LENGTH,
        MAX_HIERARCHY_NODE_NAME=MAX_HIERARCHY_NODE_NAME,
        MAX_SEARCH=MAX_SEARCH,
        MAX_SOURCE_TIMEZONE_LENGTH=MAX_SOURCE_TIMEZONE_LENGTH,
        MAX_FIELD_DEFINITION=MAX_FIELD_DEFINITION,
        MAX_FIELD_UNIT=MAX_FIELD_UNIT,
    )

    @app.template_filter("record_value_text")
    def record_value_text(value, data_type):
        return "" if value is None else get_datatype(data_type).text(value)

    @app.template_filter("absolute_utc")
    def absolute_utc(value):
        if value.utcoffset() is None:
            raise ValueError("Database timestamps must be timezone-aware")
        value = value.astimezone(UTC)
        return f"{value.day} {MONTHS[value.month - 1]} {value.year}, {value:%H:%M} UTC"

    @app.template_filter("short_date")
    def short_date(value):
        return f"{value.day} {MONTHS[value.month - 1]} {value.year}"
