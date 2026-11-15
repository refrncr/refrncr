"""Canonical Field types, independent of interfaces and database engines.

The historical Decimal ID is ``number``. Keys for the original four types
remain byte-compatible; key encoding is distinct from canonical display text.
"""

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import MappingProxyType

MAX_INPUT_LENGTH = 4096
MAX_STRING_BYTES = 1000
MAX_INTEGER_DIGITS = 1000
MAX_DECIMAL_INTEGER_DIGITS = 20
MAX_DECIMAL_SCALE = 18
MAX_FLOAT_DIGITS = 1000
MAX_FLOAT_EXPONENT = 400
MAX_FRACTION_DIGITS = 6


class DatatypeError(ValueError):
    """Bounded public message and safe classification; never holds submitted data."""

    def __init__(self, message: str, code: str = "invalid_value") -> None:
        super().__init__(message)
        self.code = code


def _string(raw: str) -> str | None:
    value = unicodedata.normalize("NFC", raw.strip())
    if not value:
        return None
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise DatatypeError("Control characters are not allowed")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise DatatypeError("Enter valid Unicode text") from None
    if len(encoded) > MAX_STRING_BYTES:
        raise DatatypeError("Value must not exceed 1000 UTF-8 bytes", "limit_exceeded")
    return value


def _integer(raw: str) -> int:
    if re.fullmatch(r"-?[0-9]+", raw) is None:
        raise DatatypeError("Enter a base-10 integer without plus, exponent or separators")
    if len(raw.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise DatatypeError("Integer must not exceed 1000 digits", "limit_exceeded")
    return int(raw)


def _decimal(raw: str) -> Decimal:
    if re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", raw) is None:
        raise DatatypeError("Enter a decimal number without exponent or separators")
    integer, _, fraction = raw.lstrip("-").partition(".")
    if len(integer) > MAX_DECIMAL_INTEGER_DIGITS or len(fraction) > MAX_DECIMAL_SCALE:
        raise DatatypeError(
            "Number must have at most 20 integer and 18 fractional digits", "limit_exceeded"
        )
    value = Decimal(raw)
    return Decimal(0) if value == 0 else value


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _float(raw: str) -> float:
    match = re.fullmatch(r"-?([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?", raw)
    if match is None:
        raise DatatypeError("Enter a finite number, optionally with a decimal exponent")
    integer, fraction, exponent = match.groups()
    if len(integer) + len(fraction or "") > MAX_FLOAT_DIGITS:
        raise DatatypeError("Float must not exceed 1000 mantissa digits", "limit_exceeded")
    if exponent is not None and (
        len(exponent.lstrip("+-")) > 3 or abs(int(exponent)) > MAX_FLOAT_EXPONENT
    ):
        raise DatatypeError("Float exponent must be between -400 and 400", "limit_exceeded")
    value = float(raw)
    if not math.isfinite(value):
        raise DatatypeError("Float must be finite and within double range")
    if value == 0 and any(character != "0" for character in integer + (fraction or "")):
        raise DatatypeError("Float is too small to represent without becoming zero")
    return 0.0 if value == 0 else value


def _boolean(raw: str) -> bool:
    if raw not in {"true", "false"}:
        raise DatatypeError("Choose true or false")
    return raw == "true"


_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_TIME = rf"[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}(?:\.[0-9]{{1,{MAX_FRACTION_DIGITS}}})?"


def _date(raw: str) -> date:
    if re.fullmatch(_DATE, raw) is None:
        raise DatatypeError("Enter a date as YYYY-MM-DD")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise DatatypeError("Enter a valid Gregorian date") from None


def _time(raw: str) -> time:
    if re.fullmatch(_TIME, raw) is None:
        raise DatatypeError("Enter HH:mm:ss with at most 6 fractional digits and no timezone")
    try:
        return time.fromisoformat(raw)
    except ValueError:
        raise DatatypeError("Enter a valid time of day") from None


def _datetime(raw: str) -> datetime:
    if re.fullmatch(rf"{_DATE}T{_TIME}", raw) is None:
        raise DatatypeError("Enter YYYY-MM-DDTHH:mm:ss with at most 6 fractional digits, no zone")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise DatatypeError("Enter a valid local date and time") from None


def _timestamp(raw: str) -> datetime:
    if re.fullmatch(rf"{_DATE}T{_TIME}(Z|[+-][0-9]{{2}}:[0-9]{{2}})", raw) is None:
        raise DatatypeError("Enter an RFC 3339 timestamp with Z or an explicit numeric offset")
    if not raw.endswith("Z"):
        offset = raw[-6:]
        if int(offset[1:3]) > 23 or int(offset[4:]) > 59:
            raise DatatypeError("Timestamp offset must be within 23:59")
    try:
        return datetime.fromisoformat(raw).astimezone(UTC)
    except (ValueError, OverflowError):
        raise DatatypeError("Enter a valid instant within UTC years 0001 to 9999") from None


def _temporal_text(value: time | datetime) -> str:
    text = value.isoformat(timespec="microseconds" if value.microsecond else "seconds")
    return text.rstrip("0").rstrip(".") if value.microsecond else text


def _timestamp_text(value: datetime) -> str:
    return _temporal_text(value.astimezone(UTC).replace(tzinfo=None)) + "Z"


@dataclass(frozen=True, slots=True)
class Datatype:
    id: str
    label: str
    representation: str
    parser: Callable
    formatter: Callable
    python_type: type
    key_prefix: bytes
    limits: str
    identifier_eligible: bool = True
    null_behavior: str = "Empty text or None is null; String also strips whitespace"

    def canonicalize(self, raw: str | None) -> tuple[object, bytes] | None:
        if raw is None or raw == "":
            return None
        if not isinstance(raw, str):
            raise DatatypeError("Value must be text")
        if len(raw) > MAX_INPUT_LENGTH:
            raise DatatypeError("Value must not exceed 4096 input characters", "limit_exceeded")
        value = self.parser(raw)
        if value is None:
            return None
        text = self.formatter(value)
        payload = ("1" if value else "0") if self.id == "boolean" else text
        return value, self.key_prefix + payload.encode("utf-8")

    def text(self, value: object) -> str:
        """Render a typed value through the same canonical contract, without coercion."""
        self.validate_typed(value)
        try:
            raw = self.formatter(value)
        except (ValueError, OverflowError):
            raise DatatypeError("Invalid typed value") from None
        parsed = self.canonicalize(raw)
        if parsed is None:
            raise DatatypeError("Invalid typed value")
        return self.formatter(parsed[0])

    def validate_typed(self, value: object) -> None:
        """Check type and formatter bounds before allocating a textual representation.

        Export serializers also use this boundary for existing typed values. String
        escaping/quoting remains their responsibility; ingress uses canonicalize.
        """
        if type(value) is not self.python_type:
            raise DatatypeError("Invalid typed value")
        if self.id == "integer" and abs(value) >= 10**MAX_INTEGER_DIGITS:
            raise DatatypeError("Integer must not exceed 1000 digits", "limit_exceeded")
        if self.id == "number":
            if not value.is_finite():
                raise DatatypeError("Invalid typed value")
            _, digits, exponent = value.as_tuple()
            if (
                len(digits) > MAX_DECIMAL_INTEGER_DIGITS + MAX_DECIMAL_SCALE
                or exponent < -MAX_DECIMAL_SCALE
                or exponent > MAX_DECIMAL_INTEGER_DIGITS
                or value.adjusted() >= MAX_DECIMAL_INTEGER_DIGITS
            ):
                raise DatatypeError("Invalid typed Decimal bounds", "limit_exceeded")
        if self.id == "float" and not math.isfinite(value):
            raise DatatypeError("Invalid typed value")
        if self.id in {"time", "datetime"} and value.tzinfo is not None:
            raise DatatypeError("Local time must not contain a timezone")
        if self.id == "timestamp" and (value.tzinfo is None or value.utcoffset() is None):
            raise DatatypeError("Timestamp requires an exact instant")

    def from_key(self, key: bytes) -> object:
        """Reject malformed/noncanonical stored keys, including wrong type prefixes."""
        try:
            if not isinstance(key, bytes) or len(key) > 1010 or not key.startswith(self.key_prefix):
                raise ValueError
            text = key[1:].decode("utf-8")
            if self.id == "boolean":
                text = {"1": "true", "0": "false"}[text]
            result = self.canonicalize(text)
            if result is None or result[1] != key:
                raise ValueError
            return result[0]
        except (ValueError, KeyError):
            raise DatatypeError("Invalid stored canonical value", "invalid_storage") from None

    def ordering_key(self, value: object) -> tuple:
        """Typed ascending order, null last; equality uses the same normalized value."""
        if value is None:
            return (1, None)
        normalized = self.canonicalize(self.text(value))[0]
        return (0, normalized.encode("utf-8") if self.id == "string" else normalized)


DATATYPES = MappingProxyType(
    {
        datatype.id: datatype
        for datatype in (
            Datatype(
                "string", "String", "NFC Unicode text", _string, str, str, b"s", "1000 UTF-8 bytes"
            ),
            Datatype(
                "integer", "Integer", "Ungrouped base 10", _integer, str, int, b"i", "1000 digits"
            ),
            Datatype(
                "number",
                "Decimal",
                "Exact base 10, no exponent",
                _decimal,
                _decimal_text,
                Decimal,
                b"n",
                "20 integer + 18 fractional digits; 38 total",
            ),
            Datatype(
                "float",
                "Float",
                "Shortest round-trip Python float repr",
                _float,
                repr,
                float,
                b"f",
                "1000 mantissa digits; exponent -400..400; finite double",
                False,
            ),
            Datatype(
                "boolean",
                "Boolean",
                "true or false",
                _boolean,
                lambda value: "true" if value else "false",
                bool,
                b"b",
                "Two values",
            ),
            Datatype(
                "date",
                "Date",
                "YYYY-MM-DD",
                _date,
                date.isoformat,
                date,
                b"d",
                "Gregorian years 0001..9999",
            ),
            Datatype(
                "time",
                "Time",
                "HH:mm:ss[.fraction]",
                _time,
                _temporal_text,
                time,
                b"t",
                "00:00:00..23:59:59.999999; no zone",
            ),
            Datatype(
                "datetime",
                "DateTime",
                "YYYY-MM-DDTHH:mm:ss[.fraction]",
                _datetime,
                _temporal_text,
                datetime,
                b"l",
                "Gregorian years 0001..9999; 6 fraction digits; no zone",
            ),
            Datatype(
                "timestamp",
                "Timestamp",
                "YYYY-MM-DDTHH:mm:ss[.fraction]Z",
                _timestamp,
                _timestamp_text,
                datetime,
                b"z",
                "UTC years 0001..9999; 6 fraction digits; offset <=23:59",
            ),
        )
    }
)
DATASET_FIELD_TYPES = tuple(DATATYPES)


def get_datatype(type_id: str) -> Datatype:
    if not isinstance(type_id, str) or len(type_id) > 20 or type_id not in DATATYPES:
        raise DatatypeError("Field type is unknown", "unknown_type")
    return DATATYPES[type_id]


def canonicalize(type_id: str, raw: str | None) -> tuple[object, bytes] | None:
    return get_datatype(type_id).canonicalize(raw)
