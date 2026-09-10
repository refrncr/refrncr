"""REF-145 canonical contracts, independent of persistence and interface frameworks."""

import math
import random
import struct
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from refrncr.datatypes import DATATYPES, DatatypeError, canonicalize, get_datatype
from refrncr.services import ValidationError, canonicalize_record_value

CASES = [
    ("string", " Cafe\u0301 ", "Café", "Café"),
    (
        "integer",
        "000123456789012345678901234567890",
        "123456789012345678901234567890",
        123456789012345678901234567890,
    ),
    (
        "number",
        "12345678901234567890.123456789012345678",
        "12345678901234567890.123456789012345678",
        Decimal("12345678901234567890.123456789012345678"),
    ),
    ("float", "1.2345678901234567e2", "123.45678901234567", 123.45678901234567),
    ("boolean", "false", "false", False),
    ("date", "2024-02-29", "2024-02-29", date(2024, 2, 29)),
    ("time", "23:59:59.120000", "23:59:59.12", time(23, 59, 59, 120000)),
    (
        "datetime",
        "2024-02-29T01:02:03.000001",
        "2024-02-29T01:02:03.000001",
        datetime(2024, 2, 29, 1, 2, 3, 1),
    ),
    (
        "timestamp",
        "2024-03-01T00:30:00.123400+01:00",
        "2024-02-29T23:30:00.1234Z",
        datetime(2024, 2, 29, 23, 30, 0, 123400, tzinfo=UTC),
    ),
]


def test_exact_catalog_and_framework_independent_metadata():
    assert [(item.id, item.label) for item in DATATYPES.values()] == [
        ("string", "String"),
        ("integer", "Integer"),
        ("number", "Decimal"),
        ("float", "Float"),
        ("boolean", "Boolean"),
        ("date", "Date"),
        ("time", "Time"),
        ("datetime", "DateTime"),
        ("timestamp", "Timestamp"),
    ]
    assert len({item.key_prefix for item in DATATYPES.values()}) == 9
    for item in DATATYPES.values():
        assert item.limits and item.representation and item.null_behavior
        assert item.identifier_eligible == (item.id != "float")
    with pytest.raises(TypeError):
        DATATYPES["uuid"] = get_datatype("string")


@pytest.mark.parametrize("type_id,raw,text,value", CASES)
def test_canonical_forms_typed_values_idempotence_and_storage(type_id, raw, text, value):
    datatype = get_datatype(type_id)
    typed, key = canonicalize(type_id, raw)
    assert type(typed) is type(value)
    assert typed == value
    assert datatype.text(typed) == text
    assert canonicalize(type_id, text) == (typed, key)
    assert datatype.from_key(key) == typed
    assert canonicalize_record_value(type_id, raw) == (typed, key)


@pytest.mark.parametrize("type_id", DATATYPES)
@pytest.mark.parametrize("raw", [None, ""])
def test_null_contract(type_id, raw):
    assert canonicalize(type_id, raw) is None
    assert get_datatype(type_id).ordering_key(None) == (1, None)


@pytest.mark.parametrize("type_id", DATATYPES)
def test_input_limit_and_nontext(type_id):
    with pytest.raises(DatatypeError) as error:
        canonicalize(type_id, "0" * 4097)
    assert error.value.code == "limit_exceeded"
    for raw in (True, False, 1, 1.5, [], {}):
        with pytest.raises(DatatypeError, match="text"):
            canonicalize(type_id, raw)


@pytest.mark.parametrize("type_id", ["decimal", "Number", "uuid", "", None, [], "x" * 10000])
def test_unknown_types_fail_even_for_null(type_id):
    with pytest.raises(DatatypeError) as error:
        canonicalize(type_id, None)
    assert error.value.code == "unknown_type"
    with pytest.raises(ValidationError):
        canonicalize_record_value(type_id, "")


@pytest.mark.parametrize(
    "type_id,raw",
    [
        ("string", "hello\x00world"),
        ("string", "x\ny"),
        ("string", "\ud800"),
        ("string", "é" * 501),
        ("integer", "+1"),
        ("integer", "1.0"),
        ("integer", "1e2"),
        ("integer", "١"),
        ("integer", "1,000"),
        ("integer", "0" * 1001),
        ("number", "01"),
        ("number", "+1"),
        ("number", "1e0"),
        ("number", "1."),
        ("number", ".1"),
        ("number", "9" * 21),
        ("number", "0." + "1" * 19),
        ("float", "NaN"),
        ("float", "Infinity"),
        ("float", "-Infinity"),
        ("float", "1e309"),
        ("float", "1e-400"),
        ("float", "1e401"),
        ("float", "0e-401"),
        ("float", "1e+"),
        ("float", "1e0000"),
        ("float", "1_000"),
        ("float", "0x1p0"),
        ("float", "9" * 1001),
        ("boolean", "TRUE"),
        ("boolean", "yes"),
        ("boolean", "1"),
        ("date", "1900-02-29"),
        ("date", "2024-02-30"),
        ("date", "0000-01-01"),
        ("date", "20240101"),
        ("date", "2024-01-01T00:00:00"),
        ("time", "12:30"),
        ("time", "24:00:00"),
        ("time", "23:59:60"),
        ("time", "01:60:00"),
        ("time", "00:00:00.1234567"),
        ("time", "12:00:00Z"),
        ("time", "12:00:00+01:00"),
        ("datetime", "2024-01-01 12:00:00"),
        ("datetime", "2024-01-01T12:00:00Z"),
        ("datetime", "2024-01-01T12:00:00+01:00"),
        ("datetime", "2023-02-29T12:00:00"),
        ("timestamp", "2024-01-01T12:00:00"),
        ("timestamp", "2024-01-01T12:00:00+0:00"),
        ("timestamp", "2024-01-01T12:00:00+24:00"),
        ("timestamp", "2024-01-01T12:00:00+01:60"),
        ("timestamp", "2024-01-01T12:00:00.1234567Z"),
        ("timestamp", "0001-01-01T00:00:00+00:01"),
        ("timestamp", "9999-12-31T23:59:59-00:01"),
        ("timestamp", "1704110400"),
    ],
)
def test_invalid_and_bounded_forms(type_id, raw):
    with pytest.raises(DatatypeError) as error:
        canonicalize(type_id, raw)
    assert len(str(error.value)) < 150
    assert error.value.code in {"invalid_value", "limit_exceeded"}


@pytest.mark.parametrize("type_id,raw,text,value", CASES[1:])
@pytest.mark.parametrize("extra", [" ", "\u00a0", "\u2003", "\t", "\n", "\x00", "junk"])
def test_nonstring_whitespace_controls_and_trailing_content(type_id, raw, text, value, extra):
    for modified in (extra + raw, raw + extra):
        with pytest.raises(DatatypeError):
            canonicalize(type_id, modified)


def test_string_has_no_semantic_validation_and_preserves_existing_whitespace_rules():
    for raw in ("not-an-email", "invalid://url", "not-a-uuid", "💡", "é" * 500):
        assert canonicalize("string", raw)[0] == raw
    assert canonicalize("string", "\u2003\t") is None
    assert canonicalize("string", "\u2003text\n")[0] == "text"


@pytest.mark.parametrize(
    "type_id,variants,expected",
    [
        ("integer", ["0", "-0", "000", "-000"], b"i0"),
        ("number", ["0", "-0", "0.000", "-0.000"], b"n0"),
        ("float", ["0", "-0", "0.0", "-0e300"], b"f0.0"),
        ("time", ["00:00:00", "00:00:00.000000"], b"t00:00:00"),
        (
            "timestamp",
            ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00+01:00", "2024-01-01T00:00:00-00:00"],
            b"z2024-01-01T00:00:00Z",
        ),
    ],
)
def test_equivalent_representations_have_identical_keys(type_id, variants, expected):
    assert {canonicalize(type_id, raw)[1] for raw in variants} == {expected}


def test_decimal_never_rounds_under_small_context():
    with localcontext() as context:
        context.prec = 2
        value, key = canonicalize("number", "12345678901234567890.123456789012345678")
        assert str(value) == "12345678901234567890.123456789012345678"
        assert key == b"n12345678901234567890.123456789012345678"
    assert len(str(canonicalize("integer", "9" * 1000)[0])) == 1000


def test_float_roundtrip_for_double_boundaries_and_deterministic_bit_samples():
    rng = random.Random(145)
    values = [0.0, -0.0, 5e-324, 2.2250738585072014e-308, 1.7976931348623157e308]
    values += [struct.unpack("!d", rng.randbytes(8))[0] for _ in range(2000)]
    for value in values:
        if not math.isfinite(value):
            continue
        typed, key = canonicalize("float", repr(value))
        assert typed == value
        assert float(key[1:]) == value
        assert get_datatype("float").from_key(key) == value
    assert math.copysign(1, canonicalize("float", "-0")[0]) == 1


@pytest.mark.parametrize(
    "type_id,raw",
    [
        ("date", "2000-02-29"),
        ("date", "0001-01-01"),
        ("date", "9999-12-31"),
        ("time", "23:59:59.999999"),
        ("datetime", "0001-01-01T00:00:00"),
        ("timestamp", "2024-01-01T23:59:00+23:59"),
        ("timestamp", "2024-01-01T00:00:00-23:59"),
    ],
)
def test_temporal_valid_boundaries(type_id, raw):
    value, key = canonicalize(type_id, raw)
    assert get_datatype(type_id).from_key(key) == value


@pytest.mark.parametrize(
    "type_id,key",
    [
        ("integer", b"i01"),
        ("integer", b"s1"),
        ("number", b"n1.0"),
        ("boolean", b"btrue"),
        ("boolean", b"b2"),
        ("float", b"f-0.0"),
        ("float", b"fNaN"),
        ("string", b"s\xff"),
        ("string", b"s x"),
        ("date", b"d2024-02-30"),
        ("time", b"t12:00:00.0"),
        ("datetime", b"l2024-01-01 12:00:00"),
        ("timestamp", b"z2024-01-01T01:00:00+01:00"),
    ],
)
def test_malformed_canonical_storage_fails_closed(type_id, key):
    with pytest.raises(DatatypeError) as error:
        get_datatype(type_id).from_key(key)
    assert error.value.code == "invalid_storage"


@pytest.mark.parametrize(
    "type_id,raw_values",
    [
        ("string", ["A", "Z", "a", "é"]),
        ("integer", ["-100", "-2", "2", "10"]),
        ("number", ["-100", "-2.1", "2.01", "10"]),
        ("float", ["-1e20", "-2.1", "5e-324", "2", "10"]),
        ("boolean", ["false", "true"]),
        ("date", ["0001-01-01", "2000-02-29", "9999-12-31"]),
        ("time", ["00:00:00", "00:00:00.1", "00:00:01", "23:59:59"]),
        ("datetime", ["2024-01-01T00:00:00.1", "2024-01-01T00:00:01"]),
        ("timestamp", ["2024-01-01T01:00:00+02:00", "2024-01-01T00:00:00Z"]),
    ],
)
def test_typed_ordering_and_nulls(type_id, raw_values):
    datatype = get_datatype(type_id)
    expected = [canonicalize(type_id, raw)[0] for raw in raw_values] + [None]
    assert sorted(reversed(expected), key=datatype.ordering_key) == expected


@pytest.mark.parametrize(
    "type_id,value",
    [
        ("integer", 10**5000),
        ("number", Decimal("1e1000000000")),
        ("number", Decimal("1e-1000000000")),
        ("number", Decimal("0e1000000000")),
        ("number", Decimal("0.1234567890123456789")),
        ("number", Decimal("sNaN")),
        ("float", float("-inf")),
        ("timestamp", datetime.min.replace(tzinfo=timezone(timedelta(minutes=1)))),
        ("timestamp", datetime.max.replace(tzinfo=timezone(timedelta(minutes=-1)))),
    ],
    ids=[
        "huge-int",
        "huge-decimal",
        "tiny-decimal",
        "huge-zero",
        "scale",
        "snan",
        "inf",
        "utc-min",
        "utc-max",
    ],
)
def test_typed_formatter_bounds_and_errors(type_id, value):
    # Huge exponents must be rejected before fixed-point formatting allocates memory.
    with pytest.raises(DatatypeError) as error:
        get_datatype(type_id).text(value)
    assert len(str(error.value)) < 150


@pytest.mark.parametrize("type_id", DATATYPES)
def test_typed_formatter_rejects_cross_type_values(type_id):
    for value in (True, 1, 1.0, Decimal("1"), "1", date(2024, 1, 1)):
        if type(value) is not get_datatype(type_id).python_type:
            with pytest.raises(DatatypeError):
                get_datatype(type_id).text(value)
