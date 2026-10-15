"""REF-158 composition, browser form integrity and unchanged import semantics."""

from html.parser import HTMLParser

import pytest
from support import html_elements
from werkzeug.datastructures import MultiDict

from refrncr.source_formats import (
    SourceFormatError,
    canonical_source,
    compose_format,
    format_components,
    formats_for,
    propose_format,
)

TEMPORAL = [
    (datatype, profile)
    for datatype in ("date", "time", "datetime", "timestamp")
    for profile in formats_for(datatype)
]


@pytest.mark.parametrize(
    "datatype,profile", TEMPORAL, ids=lambda value: getattr(value, "id", value)
)
def test_every_temporal_identifier_round_trip(datatype, profile):
    parts = format_components(datatype, profile.id)
    assert compose_format(datatype, parts) == profile.id
    assert format_components(datatype, compose_format(datatype, parts)) == parts
    if profile.kind == "datetime":
        assert parts == {
            "source": "text",
            "date": "v1-date-"
            + profile.id.removeprefix("v1-datetime-").removesuffix("-" + profile.time_style),
            "time": "v1-time-" + profile.time_style,
        }


@pytest.mark.parametrize(
    "datatype,components",
    [
        ("datetime", {"source": "text"}),
        ("datetime", {"source": "text", "date": "v1-date-ymd-dash"}),
        ("datetime", {"source": "text", "date": "v1-date-ymd-dash", "time": "v1-time-unknown"}),
        ("datetime", {"source": "native", "date": "v1-date-ymd-dash"}),
        ("date", {"source": "text", "time": "v1-time-hms"}),
        ("time", {"source": "text", "date": "v1-date-ymd-dash"}),
        ("datetime", {"source": "offset"}),
        ("number", {"source": "text", "date": "v1-date-ymd-dash"}),
        ("string", {"source": "native"}),
        ("timestamp", {"source": "offset", "time": "v1-time-hms"}),
    ],
)
def test_impossible_or_incomplete_components_rejected(datatype, components):
    with pytest.raises(SourceFormatError):
        compose_format(datatype, components)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("102.1059", "102.1059"),
        ("6.7e+07", "67000000.0"),
        ("1.25e6", "1250000.0"),
        ("6.02E-4", "0.000602"),
        ("-6.02e-4", "-0.000602"),
    ],
)
def test_float_notations_share_one_profile(raw, expected):
    assert canonical_source("float", raw, "v1-float") == expected
    choice, error = propose_format("float", ["102.1059", raw])
    assert choice == {"profile_id": "v1-float"} and error is None
    if "e" in raw.lower():
        for profile in formats_for("number"):
            with pytest.raises(SourceFormatError):
                canonical_source("number", raw, profile.id)


@pytest.mark.parametrize(
    "raw", ["NaN", "Infinity", "+Infinity", "-Infinity", "1e+", "1e--2", "1e309"]
)
def test_nonfinite_and_malformed_float_rejected(raw):
    with pytest.raises(SourceFormatError):
        canonical_source("float", raw, "v1-float")


class Controls(HTMLParser):
    """Read actual successful server-rendered controls, without a browser dependency."""

    def __init__(self, html):
        super().__init__()
        self.data = MultiDict()
        self.in_form = False
        self.current = None
        self.first = None
        self.selected = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = "data-import-mapping" in attrs
        if not self.in_form:
            return
        if (
            tag == "input"
            and attrs.get("name")
            and "disabled" not in attrs
            and (attrs.get("type") not in {"radio", "checkbox"} or "checked" in attrs)
        ):
            self.data.add(attrs["name"], attrs.get("value", ""))
        if tag == "select":
            self.current = attrs.get("name") if "disabled" not in attrs else None
            self.first = self.selected = None
        if tag == "option" and self.current:
            value = attrs.get("value", "")
            if self.first is None:
                self.first = value
            if "selected" in attrs:
                self.selected = value

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False
        if tag == "select" and self.current:
            self.data.add(self.current, self.selected if self.selected is not None else self.first)
            self.current = None


def temporal_upload(wizard, raw="2024-02-29T12:34:56", datatype="datetime"):
    wizard.services.dataset_fields.create_field(
        wizard.admin.id, wizard.dataset.key, "moment", "Moment", datatype, False, False
    )
    return wizard.upload(f"code;amount;moment\nA;1;{raw}\n".encode())


def test_detected_components_submit_and_survive_back_navigation(wizard):
    page = temporal_upload(wizard)
    data = Controls(page.text).data
    assert data["source_2"] == "text"
    assert data["date_2"] == "v1-date-ymd-dash"
    assert data["time_2"] == "v1-time-hms"
    assert "v1-datetime-ymd-dash-hms" not in page.text
    response = wizard.post(wizard.path + "/preview", data)
    assert "Proposed Record differences" in response.text
    back = Controls(wizard.get(wizard.path + "/step/3").text).data
    assert back == data
    assert wizard.count() == 0


@pytest.mark.parametrize(
    "change",
    [
        "missing_date",
        "missing_time",
        "duplicate_date",
        "duplicate_source",
        "wrong_type",
        "unknown",
        "extra",
        "canonical_with_parts",
        "native_with_parts",
        "timezone",
        "fold",
    ],
)
@pytest.mark.parametrize("endpoint", ["/formats", "/preview"])
def test_crafted_component_submissions_fail_closed(wizard, change, endpoint):
    data = Controls(temporal_upload(wizard).text).data
    if change.startswith("missing_"):
        del data[change.removeprefix("missing_") + "_2"]
    elif change.startswith("duplicate_"):
        name = change.removeprefix("duplicate_") + "_2"
        data.add(name, data[name])
    elif change == "wrong_type":
        data["time_2"] = "v1-date-ymd-dash"
    elif change == "unknown":
        data["time_2"] = "v1-time-forged"
    elif change == "extra":
        data["date_999"] = "v1-date-ymd-dash"
    elif change == "canonical_with_parts":
        data["source_2"] = "v1-datetime-ymd-dash-hms"
    elif change == "native_with_parts":
        data["source_2"] = "native"
    elif change == "timezone":
        data["timezone_2"] = "Europe/Amsterdam"
    else:
        data["fold_2"] = "earlier"
    assert wizard.post(wizard.path + endpoint, list(data.items(multi=True))).status_code == 400
    assert wizard.count() == 0
    assert wizard.post(wizard.path + "/confirm").status_code == 404


def test_offset_rejects_timezone_and_local_timestamp_retains_dst_decisions(wizard):
    data = Controls(temporal_upload(wizard, "2024-10-27 02:30:00", "timestamp").text).data
    for key, value in {
        "source_2": "text",
        "date_2": "v1-date-ymd-dash",
        "time_2": "v1-time-hms",
        "timezone_2": "Europe/Amsterdam",
    }.items():
        data[key] = value
    response = wizard.post(wizard.path + "/formats", data)
    assert "DST fold" in response.text
    for fold, expected in [("earlier", "00"), ("later", "01")]:
        data["fold_2"] = fold
        response = wizard.post(wizard.path + "/preview", data)
        assert f"2024-10-27T{expected}:30:00Z" in response.text
    data["source_2"] = "offset"
    del data["date_2"], data["time_2"]
    assert wizard.post(wizard.path + "/formats", data).status_code == 400


def test_changed_format_invalidates_prior_confirmation(wizard):
    data = Controls(temporal_upload(wizard).text).data
    assert "Proposed Record differences" in wizard.post(wizard.path + "/preview", data).text
    assert wizard.get(wizard.path + "/step/5").status_code == 200
    data["time_2"] = "v1-time-hm"
    assert "Proposed Record differences" not in wizard.post(wizard.path + "/preview", data).text
    assert wizard.post(wizard.path + "/confirm").status_code == 404
    assert wizard.count() == 0


@pytest.mark.parametrize("profile", formats_for("datetime"), ids=lambda profile: profile.id)
def test_remembered_datetime_initializes_all_components(profile):
    from datetime import datetime
    from types import SimpleNamespace

    from refrncr.record_import import DatasetRecordImportService, ParsedSource, SourceRow

    if profile.kind == "native":
        value = datetime(2024, 2, 29, 21, 5)
    else:
        parts = {"y": "2024", "m": "02", "d": "29"}
        value = profile.date_separator.join(parts[part] for part in profile.date_order)
        value += (
            " "
            + {"hm": "21:05", "hms": "21:05:00.123456", "hm12": "9:05 PM", "hms12": "9:05:00 PM"}[
                profile.time_style
            ]
        )
    source = ParsedSource("csv", ("moment",), (SourceRow(2, (str(value),), native=(value,)),), 0)
    field = SimpleNamespace(key="moment", data_type="datetime")
    columns = DatasetRecordImportService._format_columns(
        source, [field], ("moment",), {}, {"moment": {"profile_id": profile.id}}
    )
    assert columns[0]["error"] is None
    assert columns[0]["choice"] == {"profile_id": profile.id}
    assert compose_format("datetime", columns[0]["temporal"]["components"]) == profile.id


def test_incompatible_remembered_datetime_falls_back_or_requires_choice():
    remembered = {"profile_id": "v1-datetime-dmy-slash-hms12"}
    assert propose_format("datetime", ["2024-02-29T12:34:56"], remembered)[0] == {
        "profile_id": "v1-datetime-ymd-dash-hms"
    }
    for values in [["01/02/2024 12:34:56"], ["not a date"], ["2024-02-29T12:34:56", "invalid"]]:
        choice, error = propose_format("datetime", values, remembered)
        assert choice == {} and error


def test_native_excel_controls_preview_and_textual_rejection(wizard):
    import io
    from datetime import datetime

    from openpyxl import Workbook

    wizard.services.dataset_fields.create_field(
        wizard.admin.id, wizard.dataset.key, "moment", "Moment", "datetime", False, False
    )
    book = Workbook()
    book.active.append(["code", "amount", "moment"])
    book.active.append(["A", 1, datetime(2024, 2, 29, 12, 34, 56)])
    output = io.BytesIO()
    book.save(output)
    book.close()
    data = Controls(wizard.upload(output.getvalue(), "native.xlsx").text).data
    assert data["source_2"] == "native"
    assert "date_2" not in data and "time_2" not in data
    assert "2024-02-29T12:34:56" in wizard.post(wizard.path + "/preview", data).text
    data["source_2"] = "text"
    data["date_2"], data["time_2"] = "v1-date-ymd-dash", "v1-time-hms"
    assert "Proposed Record differences" not in wizard.post(wizard.path + "/preview", data).text
    assert wizard.count() == 0


def test_float_column_mixed_notation_confirms_canonical_values(wizard, import_context):
    wizard.services.dataset_fields.create_field(
        wizard.admin.id, wizard.dataset.key, "measure", "Measure", "float", False, False
    )
    page = wizard.upload(
        b"code;amount;measure\nA;1;102.1059\nB;1;6.7e+07\nC;1;1.25e6\nD;1;6.02E-4\n"
    )
    data = Controls(page.text).data
    assert data["source_2"] == "v1-float"
    assert "Finite decimal or scientific number" in page.text
    assert "102.1059" in page.text and "67000000.0" in page.text
    assert "Proposed Record differences" in wizard.post(wizard.path + "/preview", data).text
    assert wizard.get(wizard.path + "/step/5").status_code == 200
    assert "Added: 4" in wizard.post(wizard.path + "/confirm").text
    assert wizard.count() == 4
    from sqlalchemy import select

    from refrncr.models import DatasetField, DatasetRecordValue

    with import_context[0]() as session:
        stored = session.scalars(
            select(DatasetRecordValue.string_value)
            .join(DatasetField, DatasetField.id == DatasetRecordValue.field_id)
            .where(DatasetField.key == "measure")
        )
        assert set(stored) == {"102.1059", "67000000.0", "1250000.0", "0.000602"}


def test_ref154_catalogue_is_preserved_exactly():
    dates = [
        "ymd-dash",
        "ymd-slash",
        "ymd-compact",
        "dmy-dash",
        "dmy-slash",
        "dmy-dot",
        "mdy-dash",
        "mdy-slash",
    ]
    times = ["hm", "hms", "hm12", "hms12"]
    datetimes = {"v1-datetime-" + date + "-" + time for date in dates for time in times}
    assert {p.id for p in formats_for("date")} == {"v1-date-" + key for key in dates} | {
        "v1-excel-date"
    }
    assert {p.id for p in formats_for("time")} == {"v1-time-" + key for key in times} | {
        "v1-excel-time"
    }
    assert {p.id for p in formats_for("datetime")} == datetimes | {"v1-excel-datetime"}
    assert {p.id for p in formats_for("timestamp")} == datetimes | {
        "v1-excel-timestamp",
        "v1-offset",
    }
    for datatype in ["integer", "number"]:
        assert {p.id for p in formats_for(datatype)} == {
            "v1-plain",
            "v1-eu",
            "v1-us",
            "v1-space-comma",
            "v1-space-dot",
        }
    assert [p.id for p in formats_for("float")] == ["v1-float"]
    assert [p.id for p in formats_for("boolean")] == ["v1-boolean"]
    assert [p.id for p in formats_for("string")] == ["v1-text"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_maximum_width_component_form_preserves_upload_column_limit(wizard, ambiguous):
    from refrncr.record_import import MAX_COLUMNS

    for index in range(MAX_COLUMNS - 2):
        wizard.services.dataset_fields.create_field(
            wizard.admin.id,
            wizard.dataset.key,
            f"moment_{index}",
            f"Moment {index}",
            "datetime",
            False,
            False,
        )
    headers = ["code", "amount"] + [f"moment_{index}" for index in range(MAX_COLUMNS - 2)]
    value = "01/02/2024 12:34:56" if ambiguous else "2024-02-29T12:34:56"
    values = ["A", "1"] + [value] * (MAX_COLUMNS - 2)
    data = Controls(
        wizard.upload((";".join(headers) + "\n" + ";".join(values) + "\n").encode()).text
    ).data
    # Both text-only browser refresh and Preview use URL-encoded controls.
    response = wizard.client.post(
        wizard.path + "/formats",
        base_url="https://localhost",
        data=data,
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200
    # Count the select itself: the wrapper's data-source-type-field attribute also
    # contains "data-source-type" as a substring.
    elements = html_elements(response.text)
    assert sum(tag == "select" and "data-source-type" in attrs for tag, attrs in elements) == (
        0 if ambiguous else MAX_COLUMNS - 2
    )
    if ambiguous:
        assert sum(
            "format-reading" in attrs.get("class", "").split() for _, attrs in elements
        ) == 2 * (MAX_COLUMNS - 2)
        for index in range(2, MAX_COLUMNS):
            assert data[f"source_{index}"] == ""
            data[f"source_{index}"] = "v1-datetime-dmy-slash-hms"
    assert all(len(data.getlist(key)) == 1 for key in data)
    assert "Proposed Record differences" in wizard.post(wizard.path + "/preview", data).text
    assert wizard.count() == 0


@pytest.mark.parametrize("endpoint", ["/formats", "/preview"])
def test_component_form_envelopes_remain_bounded(wizard, endpoint):
    from refrncr.record_import import MAX_COLUMNS

    temporal_upload(wizard)
    response = wizard.client.post(
        wizard.path + endpoint,
        base_url="https://localhost",
        data=b"x=" + b"x" * (MAX_COLUMNS * 1024),
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 413
    boundary = "ref158-bounded-controls"
    body = (
        b"".join(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="source_2"\r\n'
                "\r\nv1-datetime-ymd-dash-hms\r\n"
            ).encode()
            for _ in range(MAX_COLUMNS * 6 + 3)
        )
        + f"--{boundary}--\r\n".encode()
    )
    response = wizard.client.post(
        wizard.path + endpoint,
        base_url="https://localhost",
        data=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    assert response.status_code == 413
    assert wizard.count() == 0
