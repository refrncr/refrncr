"""REF-159 presentation uses the parser's complete-column results and strict envelope."""

from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from test_import_wizard import optional_field
from test_source_format_components import Controls
from test_source_format_import import import_context as import_context
from test_source_format_import import wizard as wizard

from refrncr import source_formats
from refrncr.record_import import DatasetRecordImportService, ParsedSource, SourceRow
from refrncr.source_formats import canonical_source, formats_for, propose_format, temporal_controls


def column(datatype, values, profile=None):
    source = ParsedSource(
        "csv", ("value",), tuple(SourceRow(i + 2, (v,)) for i, v in enumerate(values)), 0
    )
    return DatasetRecordImportService._format_columns(
        source,
        [SimpleNamespace(key="value", data_type=datatype)],
        ("value",),
        {"value": {"profile_id": profile}} if profile else {},
        {},
    )[0]


@pytest.mark.parametrize(
    "datatype,values,profile,state,verdict",
    [
        (
            "date",
            ["2026-02-01"] * 200 + ["", None],
            None,
            "clean",
            "Read as YYYY-MM-DD — all 200 values parsed, stored exactly as written.",
        ),
        (
            "number",
            ["1", "1.234,56"],
            "v1-eu",
            "transformed",
            "Read as European · 1.234,56 — 1.234,56 is stored as 1234.56.",
        ),
        ("date", ["01/02/2026"], None, "undecided", None),
        (
            "date",
            ["2026-02-01"] * 197 + ["invalid"] * 3 + ["", None],
            "v1-date-ymd-dash",
            "failing",
            "3 of 200 values do not fit YYYY-MM-DD.",
        ),
    ],
)
def test_verdict_states_and_full_nonempty_population(datatype, values, profile, state, verdict):
    result = column(datatype, values, profile)
    assert result["state"] == state
    assert result["verdict"] == verdict
    assert result["total_values"] == sum(v is not None and v != "" for v in values)
    assert len(result["examples"]) <= 3
    if verdict and "stored exactly as written" in verdict:
        assert all(e["source"] == e["stored"] for e in result["examples"])
    if state == "transformed":
        assert verdict.count(" is stored as ") == 1
        assert "1 is stored as 1" not in verdict


@pytest.mark.parametrize(
    "datatype,value,count",
    [("date", "01/02/2026", 2), ("number", "1,234", 3), ("number", "1.234", 4)],
)
def test_candidate_readings_reuse_canonical_identifiers(datatype, value, count):
    result = column(datatype, [value] * 12)
    assert result["state"] == "undecided"
    assert len(result["readings"]) == count
    for reading in result["readings"]:
        assert reading["id"] in {p.id for p in formats_for(datatype)}
        if datatype == "date":
            labels = {p["id"]: p["label"] for p in temporal_controls("date", "")["dates"]}
            assert reading["label"] == labels[reading["id"]]
            assert reading["label"] in {"Day first · DD/MM/YYYY", "Month first · MM/DD/YYYY"}
        else:
            labels = {p.id: p.label for p in formats_for(datatype)}
            assert reading["label"] == labels[reading["id"]]
        assert reading["example_source"] == value
        assert reading["example_stored"] == canonical_source(datatype, value, reading["id"])
        assert len(reading["example_source"]) <= 80
        assert len(reading["example_stored"]) <= 80


def test_readings_do_not_run_a_second_parser_scan(monkeypatch):
    original = source_formats.canonical_source
    calls = []

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(source_formats, "canonical_source", counted)
    values = ["01/02/2026"] * 200
    expected = propose_format("date", values)
    baseline = list(calls)
    calls.clear()
    result = column("date", values)
    assert (result["choice"], result["error"]) == expected
    assert calls == baseline
    assert len(result["readings"]) == 2


def test_more_than_four_matches_falls_back_without_changing_detection(monkeypatch):
    # The present catalogue has at most four genuinely ambiguous matches. Exercise
    # the defensive limit with existing profiles and the unchanged real parser.
    profiles = formats_for("number")
    monkeypatch.setattr(source_formats, "formats_for", lambda _: profiles + profiles[:1])
    readings = []
    expected = propose_format("number", ["1.234"])
    assert propose_format("number", ["1.234"], readings=readings) == expected
    assert readings == []


@pytest.mark.parametrize("values", [["unknown"], ["01/02/2026", "2026-03-01"], ["", None]])
def test_unknown_mixed_and_empty_have_no_readings(values):
    assert column("date", values)["readings"] == []


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def ambiguous_page(wizard):
    optional_field(wizard, "day")
    return wizard.upload(b"code;amount;day\nA;1.234;01/02/2026\n")


def test_radio_envelope_keeps_one_empty_key_until_explicit_choice(wizard):
    page = ambiguous_page(wizard)
    elements = Elements(page.text).items
    for index, count in [(1, 4), (2, 2)]:
        controls = [
            (tag, attrs) for tag, attrs in elements if attrs.get("name") == f"source_{index}"
        ]
        assert len(controls) == count + 1
        assert all(tag == "input" and attrs["type"] == "radio" for tag, attrs in controls)
        assert sum("checked" in attrs for _, attrs in controls) == 1
        assert next(attrs for _, attrs in controls if "checked" in attrs)["value"] == ""
    data = Controls(page.text).data
    expected = {
        f"{prefix}_{i}" for prefix in ("mapping", "source", "timezone", "fold") for i in range(3)
    } | {"csrf_token"}
    assert set(data) == expected
    assert all(len(data.getlist(key)) == 1 for key in expected)
    assert wizard.post(wizard.path + "/formats", data).status_code == 200
    assert "Proposed Record differences" not in wizard.post(wizard.path + "/preview", data).text
    assert wizard.post(wizard.path + "/confirm").status_code == 404
    data["source_1"], data["source_2"] = "v1-eu", "v1-date-dmy-slash"
    assert "Proposed Record differences" in wizard.post(wizard.path + "/preview", data).text
    data["source_2"] = "v1-date-ymd-dash"
    assert "Proposed Record differences" not in wizard.post(wizard.path + "/preview", data).text
    assert wizard.post(wizard.path + "/confirm").status_code == 404
    assert wizard.count() == 0


@pytest.mark.parametrize("endpoint", ["/formats", "/preview"])
@pytest.mark.parametrize(
    "defect", ["duplicate", "missing", "extra", "unknown", "incompatible", "components"]
)
def test_radio_form_tampering_fails_closed(wizard, endpoint, defect):
    data = Controls(ambiguous_page(wizard).text).data
    data["source_1"], data["source_2"] = "v1-eu", "v1-date-dmy-slash"
    if defect == "duplicate":
        data.add("source_2", "v1-date-mdy-slash")
    elif defect == "missing":
        del data["source_2"]
    elif defect == "extra":
        data["unexpected"] = "x"
    elif defect == "components":
        data["date_2"] = "v1-date-dmy-slash"
    else:
        data["source_2"] = "v1-invented" if defect == "unknown" else "v1-float"
    assert wizard.post(wizard.path + endpoint, list(data.items(multi=True))).status_code == 400
    assert wizard.count() == 0


def test_untrusted_verdict_and_examples_are_escaped_and_bounded(wizard):
    wizard.services.dataset_fields.create_field(
        wizard.admin.id, wizard.dataset.key, "flag", "Flag", "boolean", False, False
    )
    page = wizard.upload(("code;amount;flag\nA;1;<script>" + "x" * 300 + "\n").encode())
    assert "<script>" not in page.text
    assert "&lt;script&gt;" in page.text
    data = Controls(page.text).data
    data["mapping_2"] = "flag"
    data["source_2"] = "v1-boolean"
    response = wizard.post(wizard.path + "/formats", data)
    assert response.status_code == 200
    assert "data-state='failing'" in response.text
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    result = column("boolean", ["<script>" + "x" * 300])
    assert len(result["examples"][0]["source"]) <= 80
    assert result["verdict"].startswith("1 of 1 values do not fit")


@pytest.mark.parametrize("datatype", ["datetime", "timestamp"])
def test_datetime_reading_labels_share_both_component_labels(datatype):
    profile = next(p for p in formats_for(datatype) if p.id == "v1-datetime-dmy-slash-hms")
    controls = temporal_controls(datatype, profile.id)
    expected = "Day first · DD/MM/YYYY + 24-hour, seconds · HH:mm:ss[.ffffff]"
    assert source_formats.source_format_label(profile) == expected
    assert expected == " + ".join(
        next(p["label"] for p in controls[part + "s"] if p["id"] == controls["components"][part])
        for part in ("date", "time")
    )
    if datatype == "datetime":
        assert column(datatype, ["01/02/2026 12:34:56"])["readings"][0]["label"] == expected


@pytest.mark.parametrize("raw,has_readings", [("01/02/2026", True), ("unknown", False)])
def test_undecided_guidance_has_one_message_and_retains_description_targets(
    wizard, raw, has_readings
):
    from flask import render_template

    data = column("date", [raw])
    with wizard.client.application.test_request_context():
        html = render_template("pages/datasets/import/source_format.html", column=data, index=0)
    elements = Elements(html).items
    ids = [attrs["id"] for _, attrs in elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert html.count("Choose how this column is interpreted.") == 1
    effective = [attrs for _, attrs in elements if "data-effective-format" in attrs]
    assert len(effective) == 1
    assert effective[0]["tabindex"] == "-1" and effective[0]["aria-live"] == "polite"
    for _, attrs in elements:
        for described in attrs.get("aria-describedby", "").split():
            assert described in ids
    if has_readings:
        assert "class='error-text'" not in html
        assert "Ambiguous source format;" not in html
        assert "Choose source format" not in html
        assert "id='source-error-0'" in html
        assert "aria-atomic='true'></p>" in html
    else:
        assert "class='error-text'" in html
        assert "Unknown or mixed source formats;" in html
        assert "Choose source format</p>" in html


@pytest.fixture
def native_date_source():
    from pathlib import Path

    from refrncr.record_import import parse_source

    path = Path(__file__).parent / "fixtures" / "native_excel_dates.xlsx"
    return parse_source(path.name, path.read_bytes(), "Business objects")


@pytest.mark.parametrize("target", ["date", "datetime"])
def test_workbook_native_date_verdict_and_text_datetime(native_date_source, wizard, target):
    from datetime import datetime

    from flask import render_template

    assert len(native_date_source.rows) == 24
    assert native_date_source.rows[0].source_value(1) == datetime(2026, 4, 3)
    columns = DatasetRecordImportService._format_columns(
        native_date_source,
        [
            SimpleNamespace(key="updated", data_type="datetime"),
            SimpleNamespace(key="entry", data_type=target),
        ],
        ("updated", "entry"),
        {},
        {},
    )
    text, native = columns
    assert native["state"] == "clean"
    assert native["total_values"] == 24
    expected = "2026-04-03" if target == "date" else "2026-04-03T00:00:00"
    assert native["examples"][0]["source"] == expected
    assert native["examples"][0]["stored"] == expected
    assert "3 April 2026" in native["verdict"]
    assert "no day/month guessing needed" in native["verdict"]
    assert f"stored as {expected}" in native["verdict"]
    with wizard.client.application.test_request_context():
        html = render_template("pages/datasets/import/source_format.html", column=native, index=1)
    if target == "date":
        assert "00:00:00" not in html
    else:
        assert "T00:00:00" in html
    assert text["state"] == "clean"
    assert text["examples"][0]["source"] == "2026-08-25T11:46:14.411354"
    assert text["examples"][0]["source"] == text["examples"][0]["stored"]
    assert "real Excel" not in text["verdict"]
    assert "August" not in text["verdict"]
    assert "no day/month guessing" not in text["verdict"]
    assert "all 24 values parsed, stored exactly as written" in text["verdict"]


def test_native_nonmidnight_datetime_keeps_time_and_failed_date_keeps_error():
    from datetime import datetime

    value = datetime(2026, 4, 3, 12, 34, 56)
    result = column("datetime", [value])
    assert result["state"] == "clean"
    assert result["examples"][0]["source"] == "2026-04-03T12:34:56"
    assert "3 April 2026 at 12:34:56" in result["verdict"]
    failed = column("date", [value])
    assert failed["state"] in {"undecided", "failing"}
    assert failed["examples"][0]["source"] == "2026-04-03T12:34:56"
    assert not failed["verdict"] or "no day/month guessing" not in failed["verdict"]
