"""Behavioral contract for the shared REF-154 source-format catalogue."""

from datetime import UTC, date, datetime, time
from decimal import localcontext

import pytest

from refrncr.source_formats import (
    BOOLEAN_TOKENS,
    SOURCE_FORMATS,
    SourceFormatError,
    canonical_source,
    formats_for,
    propose_format,
)


@pytest.mark.parametrize(
    "profile,raw,expected",
    [
        ("v1-plain", "1234.56", "1234.56"),
        ("v1-plain", "-1234", "-1234"),
        ("v1-eu", "1.234,56", "1234.56"),
        ("v1-us", "1,234.56", "1234.56"),
        ("v1-space-comma", "1 234,56", "1234.56"),
        ("v1-space-dot", "1 234.56", "1234.56"),
        ("v1-eu", "-123.456.789,000001", "-123456789.000001"),
    ],
)
def test_numeric_profiles(profile, raw, expected):
    assert canonical_source("number", raw, profile) == expected
    choice, error = propose_format("number", [raw])
    assert error is None
    assert canonical_source("number", raw, **choice) == expected


@pytest.mark.parametrize("profile", [p.id for p in formats_for("integer")])
def test_integer_profiles(profile):
    p = SOURCE_FORMATS[profile]
    assert canonical_source("integer", "-1" + p.group + "234", profile) == "-1234"
    with pytest.raises(SourceFormatError):
        canonical_source("integer", "1" + p.decimal + "25", profile)


@pytest.mark.parametrize("raw", ["1.234", "1,234"])
@pytest.mark.parametrize("datatype", ["integer", "number"])
def test_ambiguous_numbers_require_a_decision(raw, datatype):
    assert propose_format(datatype, [raw])[0] == {}
    assert canonical_source(datatype, raw, "v1-eu" if "." in raw else "v1-us") == "1234"


@pytest.mark.parametrize(
    "raw",
    [
        "12.34,56",
        "1.23.456,78",
        "01.234,56",
        "1..234",
        "(1.234,56)",
        "+1",
        "1e2",
        "NaN",
        "Infinity",
        "NULL",
        "N/A",
        "-",
        " 1",
        "1 ",
        "1\u00a0234,56",
    ],
)
def test_strict_numeric_grammar(raw):
    with pytest.raises(SourceFormatError):
        canonical_source("number", raw, "v1-eu")


@pytest.mark.parametrize(
    "profile,raw",
    [
        ("v1-plain", "1,234"),
        ("v1-us", "12,34.56"),
        ("v1-us", "1,23,456.78"),
        ("v1-us", "01,234.56"),
        ("v1-space-comma", "12 34,56"),
        ("v1-space-dot", "1 23 456.78"),
        ("v1-space-comma", "01 234,56"),
        ("v1-space-dot", "1  234.56"),
    ],
)
def test_invalid_grouping_in_other_numeric_profiles(profile, raw):
    with pytest.raises(SourceFormatError):
        canonical_source("number", raw, profile)


def test_exact_decimal_precision_even_under_small_context():
    with localcontext() as context:
        context.prec = 2
        assert (
            canonical_source("number", "12.345.678.901.234.567.890,123456789012345678", "v1-eu")
            == "12345678901234567890.123456789012345678"
        )
        assert canonical_source("number", "0,100000000000000001", "v1-eu") != canonical_source(
            "number", "0,100000000000000002", "v1-eu"
        )
        with pytest.raises(SourceFormatError):
            canonical_source("number", "0,1234567890123456789", "v1-eu")


@pytest.mark.parametrize(
    "raw,expected",
    [("1.25e6", "1250000.0"), ("6.02E-4", "0.000602"), ("-12", "-12.0"), ("5e-324", "5e-324")],
)
def test_float_scientific(raw, expected):
    assert canonical_source("float", raw, "v1-float") == expected


@pytest.mark.parametrize(
    "raw",
    ["1e", "1e+", "1ee2", "1e401", "1e309", "1e-400", "NaN", "nan", "Infinity", "-Infinity", "inf"],
)
def test_float_rejections(raw):
    with pytest.raises(SourceFormatError):
        canonical_source("float", raw, "v1-float")


@pytest.mark.parametrize("token,expected", BOOLEAN_TOKENS.items())
def test_boolean_tokens_case_insensitive(token, expected):
    assert canonical_source("boolean", token.upper(), "v1-boolean") == expected
    assert propose_format("boolean", list(BOOLEAN_TOKENS))[0]["profile_id"] == "v1-boolean"


@pytest.mark.parametrize(
    "key,raw",
    [
        ("ymd-dash", "2024-02-29"),
        ("ymd-slash", "2024/02/29"),
        ("ymd-compact", "20240229"),
        ("dmy-dash", "29-02-2024"),
        ("dmy-slash", "29/02/2024"),
        ("dmy-dot", "29.02.2024"),
        ("mdy-dash", "02-29-2024"),
        ("mdy-slash", "02/29/2024"),
    ],
)
def test_date_profiles_and_calendar(key, raw):
    assert canonical_source("date", raw, "v1-date-" + key) == "2024-02-29"
    with pytest.raises(SourceFormatError):
        canonical_source("date", raw.replace("2024", "2023"), "v1-date-" + key)
    assert propose_format("date", [raw])[1] is None


@pytest.mark.parametrize(
    "key,raw,expected",
    [
        ("hm", "09:05", "09:05:00"),
        ("hms", "09:05:06", "09:05:06"),
        ("hms", "09:05:06.123400", "09:05:06.1234"),
        ("hm12", "9:05 PM", "21:05:00"),
        ("hms12", "12:05:06 AM", "00:05:06"),
    ],
)
def test_time_profiles(key, raw, expected):
    assert canonical_source("time", raw, "v1-time-" + key) == expected


@pytest.mark.parametrize(
    "raw,profile",
    [
        ("24:00", "hm"),
        ("12:60", "hm"),
        ("12:01:60", "hms"),
        ("12:01:01.1234567", "hms"),
        ("0:01 PM", "hm12"),
        ("13:01 PM", "hm12"),
    ],
)
def test_time_rejections(raw, profile):
    with pytest.raises(SourceFormatError):
        canonical_source("time", raw, "v1-time-" + profile)


@pytest.mark.parametrize(
    "profile", [p for p in formats_for("datetime") if p.kind == "datetime"], ids=lambda p: p.id
)
def test_every_catalogued_datetime_combination(profile):
    parts = {"y": "2024", "m": "02", "d": "29"}
    raw = profile.date_separator.join(parts[c] for c in profile.date_order)
    raw += (
        " "
        + {"hm": "21:05", "hms": "21:05:00.123456", "hm12": "9:05 PM", "hms12": "9:05:00 PM"}[
            profile.time_style
        ]
    )
    expected = "2024-02-29T21:05:00" + (".123456" if profile.time_style == "hms" else "")
    assert canonical_source("datetime", raw, profile.id) == expected
    assert (
        canonical_source("timestamp", raw, profile.id, timezone="Europe/Amsterdam")
        == expected.replace("T21:", "T20:") + "Z"
    )


def test_iso_datetime_separator_and_fraction():
    value = "2024-02-29T21:05:06.123456"
    assert canonical_source("datetime", value, "v1-datetime-ymd-dash-hms") == value
    assert canonical_source("datetime", "2024-02-29T21:05", "v1-datetime-ymd-dash-hm") == (
        "2024-02-29T21:05:00"
    )
    with pytest.raises(SourceFormatError):
        canonical_source("datetime", "29/02/2024T21:05", "v1-datetime-dmy-slash-hm")


@pytest.mark.parametrize(
    "raw", ["2024-02-29T12:00:00Z", "2024-02-29T13:00:00+01:00", "2024-02-29T07:00:00-05:00"]
)
def test_offset_timestamp(raw):
    assert canonical_source("timestamp", raw, "v1-offset") == "2024-02-29T12:00:00Z"


def test_dst_gap_fold_and_explicit_timezone():
    profile = "v1-datetime-ymd-dash-hms"
    with pytest.raises(SourceFormatError, match="IANA"):
        canonical_source("timestamp", "2024-01-01 12:00:00", profile)
    with pytest.raises(SourceFormatError, match="gap"):
        canonical_source("timestamp", "2024-03-31 02:30:00", profile, timezone="Europe/Amsterdam")
    with pytest.raises(SourceFormatError, match="fold"):
        canonical_source("timestamp", "2024-10-27 02:30:00", profile, timezone="Europe/Amsterdam")
    for fold, expected in [("earlier", "00"), ("later", "01")]:
        assert (
            canonical_source(
                "timestamp", "2024-10-27 02:30:00", profile, timezone="Europe/Amsterdam", fold=fold
            )
            == f"2024-10-27T{expected}:30:00Z"
        )
    for zone in ["../UTC", "Unknown/Zone", "x" * 101]:
        with pytest.raises(SourceFormatError, match="IANA"):
            canonical_source("timestamp", "2024-01-01 12:00:00", profile, timezone=zone)


@pytest.mark.parametrize(
    "datatype,value,expected",
    [
        ("date", date(2024, 2, 29), "2024-02-29"),
        ("date", datetime(2024, 2, 29), "2024-02-29"),
        ("time", time(12, 34, 56, 123000), "12:34:56.123"),
        ("datetime", datetime(2024, 2, 29, 12, 34, 56), "2024-02-29T12:34:56"),
        ("timestamp", datetime(2024, 2, 29, 12, 34, 56), "2024-02-29T11:34:56Z"),
    ],
)
def test_native_excel_values(datatype, value, expected):
    assert (
        canonical_source(datatype, value, "v1-excel-" + datatype, timezone="Europe/Amsterdam")
        == expected
    )


def test_native_date_does_not_silently_discard_time():
    with pytest.raises(SourceFormatError):
        canonical_source("date", datetime(2024, 2, 29, 12), "v1-excel-date")
    with pytest.raises(SourceFormatError):
        canonical_source("datetime", datetime(2024, 2, 29, tzinfo=UTC), "v1-excel-datetime")


@pytest.mark.parametrize(
    "datatype,values",
    [
        ("date", ["01/02/2024"]),
        ("date", ["2024-01-31", "31/01/2024"]),
        ("number", ["1.234,56", "1,234.56"]),
        ("date", ["2023-02-29"]),
    ],
)
def test_ambiguous_unknown_and_mixed_columns(datatype, values):
    assert propose_format(datatype, values)[0] == {}


def test_remembered_proposal_is_used_only_for_the_whole_column():
    remembered = {"profile_id": "v1-eu"}
    assert propose_format("number", ["1.234"], remembered) == (remembered, None)
    assert propose_format("number", ["1.234", "12.34"], remembered)[0] == {"profile_id": "v1-plain"}
    assert propose_format("number", ["1.234,56", "1,234.56"], remembered)[0] == {}


@pytest.mark.parametrize(
    "datatype",
    ["string", "integer", "number", "float", "boolean", "date", "time", "datetime", "timestamp"],
)
def test_null_is_only_empty_and_never_a_special_token(datatype):
    profile = formats_for(datatype)[0].id
    assert canonical_source(datatype, "", profile) == ""
    assert canonical_source(datatype, None, profile) == ""
    for raw in ["NULL", "N/A", "-"]:
        if datatype == "string":
            assert canonical_source(datatype, raw, profile) == raw
        else:
            with pytest.raises(SourceFormatError):
                canonical_source(datatype, raw, profile)


@pytest.mark.parametrize(
    "datatype,value,expected",
    [
        ("number", 1.234, "1.234"),
        ("number", 0.0000001, "0.0000001"),
        ("integer", 1234.0, "1234"),
        ("float", 1.25e6, "1250000.0"),
        ("boolean", True, "true"),
    ],
)
def test_native_excel_numeric_cells_have_no_text_grouping_ambiguity(datatype, value, expected):
    choice, error = propose_format(datatype, [value])
    assert error is None
    assert canonical_source(datatype, value, **choice) == expected


def test_whitespace_is_not_an_empty_numeric_csv_row():
    from refrncr.record_import import parse_source

    source = parse_source("spaces.csv", b"code;amount\n; \n")
    assert len(source.rows) == 1
    assert source.rows[0].values == ("", " ")
    with pytest.raises(SourceFormatError):
        canonical_source("number", source.rows[0].values[1], "v1-plain")
