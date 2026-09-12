"""REF-154 versioned, locale-independent CSV/XLSX source grammars.

This adapter returns canonical text; the Record datatype contract remains authoritative.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from refrncr.datatypes import DatatypeError, get_datatype


class SourceFormatError(ValueError):
    """Bounded public error, never containing source content."""

    def __init__(self, message, *, requires_choice=False):
        super().__init__(message)
        self.requires_choice = requires_choice


@dataclass(frozen=True, slots=True)
class SourceFormat:
    id: str
    label: str
    types: tuple[str, ...]
    kind: str
    date_order: str = ""
    date_separator: str = ""
    time_style: str = ""
    group: str = ""
    decimal: str = "."


_DATE_PROFILES = (
    ("ymd-dash", "YYYY-MM-DD", "ymd", "-"),
    ("ymd-slash", "YYYY/MM/DD", "ymd", "/"),
    ("ymd-compact", "YYYYMMDD", "ymd", ""),
    ("dmy-dash", "DD-MM-YYYY", "dmy", "-"),
    ("dmy-slash", "DD/MM/YYYY", "dmy", "/"),
    ("dmy-dot", "DD.MM.YYYY", "dmy", "."),
    ("mdy-dash", "MM-DD-YYYY", "mdy", "-"),
    ("mdy-slash", "MM/DD/YYYY", "mdy", "/"),
)
_TIME_PROFILES = (
    ("hm", "HH:mm"),
    ("hms", "HH:mm:ss[.ffffff]"),
    ("hm12", "h:mm AM/PM"),
    ("hms12", "h:mm:ss AM/PM"),
)


def _catalogue():
    result = [
        SourceFormat("v1-plain", "Ungrouped · 1234.56", ("integer", "number"), "number"),
        SourceFormat(
            "v1-eu", "European · 1.234,56", ("integer", "number"), "number", group=".", decimal=","
        ),
        SourceFormat(
            "v1-us", "International / US · 1,234.56", ("integer", "number"), "number", group=","
        ),
        SourceFormat(
            "v1-space-comma",
            "Space grouping · 1 234,56",
            ("integer", "number"),
            "number",
            group=" ",
            decimal=",",
        ),
        SourceFormat(
            "v1-space-dot", "Space grouping · 1 234.56", ("integer", "number"), "number", group=" "
        ),
        SourceFormat("v1-float", "Finite decimal or scientific number", ("float",), "canonical"),
        SourceFormat(
            "v1-boolean", "true/false · yes/no · y/n · 1/0 · ja/nee", ("boolean",), "boolean"
        ),
        SourceFormat("v1-text", "Text", ("string",), "canonical"),
        SourceFormat("v1-offset", "ISO / RFC 3339 with Z or offset", ("timestamp",), "canonical"),
    ]
    for key, label, order, separator in _DATE_PROFILES:
        result.append(SourceFormat("v1-date-" + key, label, ("date",), "date", order, separator))
        for time_key, time_label in _TIME_PROFILES:
            result.append(
                SourceFormat(
                    "v1-datetime-" + key + "-" + time_key,
                    label + " " + time_label,
                    ("datetime", "timestamp"),
                    "datetime",
                    order,
                    separator,
                    time_key,
                )
            )
    for key, label in _TIME_PROFILES:
        result.append(SourceFormat("v1-time-" + key, label, ("time",), "time", time_style=key))
    for datatype in ("date", "time", "datetime", "timestamp"):
        result.append(
            SourceFormat("v1-excel-" + datatype, "Native Excel " + datatype, (datatype,), "native")
        )
    return MappingProxyType({profile.id: profile for profile in result})


SOURCE_FORMATS = _catalogue()
BOOLEAN_TOKENS = MappingProxyType(
    {
        token: canonical
        for pair, canonical in (
            (("true", "yes", "y", "1", "ja"), "true"),
            (("false", "no", "n", "0", "nee"), "false"),
        )
        for token in pair
    }
)


def formats_for(data_type):
    return tuple(profile for profile in SOURCE_FORMATS.values() if data_type in profile.types)


def format_components(data_type, profile_id):
    """Losslessly expose an existing profile as compact temporal choices."""
    profile = SOURCE_FORMATS.get(profile_id)
    if profile is None or data_type not in profile.types:
        raise SourceFormatError("Choose an accepted source format for this datatype.")
    if profile.kind == "native":
        return {"source": "native"}
    if profile.id == "v1-offset":
        return {"source": "offset"}
    if profile.kind not in {"date", "time", "datetime"}:
        return {"source": profile.id}
    result = {"source": "text"}
    if profile.kind in {"date", "datetime"}:
        result["date"] = next(
            p.id
            for p in formats_for("date")
            if p.kind == "date"
            and (p.date_order, p.date_separator) == (profile.date_order, profile.date_separator)
        )
    if profile.kind in {"time", "datetime"}:
        result["time"] = "v1-time-" + profile.time_style
    return result


def compose_format(data_type, components):
    """Accept only the exact components belonging to one catalogue entry."""
    for profile in formats_for(data_type):
        if format_components(data_type, profile.id) == components:
            return profile.id
    raise SourceFormatError("Choose a valid source type and its required format components.")


def temporal_controls(data_type, profile_id):
    if data_type not in {"date", "time", "datetime", "timestamp"}:
        return None
    components = format_components(data_type, profile_id) if profile_id else {"source": ""}
    labels = {
        "hm": "24-hour, minutes",
        "hms": "24-hour, seconds",
        "hm12": "12-hour, minutes",
        "hms12": "12-hour, seconds",
    }
    date_labels = {
        "ymd": "Year first",
        "dmy": "Day first",
        "mdy": "Month first",
    }
    return {
        "components": components,
        "dates": tuple(
            {
                "id": p.id,
                "label": date_labels[p.date_order] + " · " + p.label,
                "pattern": p.label,
            }
            for p in formats_for("date")
            if p.kind == "date"
        )
        if data_type != "time"
        else (),
        "times": tuple(
            {
                "id": p.id,
                "label": labels[p.time_style] + " · " + p.label,
                "pattern": p.label,
            }
            for p in formats_for("time")
            if p.kind == "time"
        )
        if data_type != "date"
        else (),
        "native_label": "Native Excel " + ("datetime" if data_type == "timestamp" else data_type),
        "native_noun": "datetime" if data_type == "timestamp" else data_type,
        "plain_noun": {
            "date": "date",
            "time": "time",
            "datetime": "date and time",
            "timestamp": "date and time",
        }[data_type],
    }


def _date(value, profile):
    parts = {"y": r"([0-9]{4})", "m": r"([0-9]{2})", "d": r"([0-9]{2})"}
    match = re.fullmatch(
        re.escape(profile.date_separator).join(parts[c] for c in profile.date_order), value
    )
    if not match:
        raise ValueError
    values = dict(zip(profile.date_order, map(int, match.groups()), strict=True))
    return date(values["y"], values["m"], values["d"])


def _time(value, style):
    twelve = style.endswith("12")
    seconds = style.startswith("hms")
    grammar = r"([0-9]{1,2})" if twelve else r"([0-9]{2})"
    grammar += r":([0-9]{2})"
    if seconds:
        grammar += r":([0-9]{2})" + ("" if twelve else r"(?:\.([0-9]{1,6}))?")
    if twelve:
        grammar += r" (AM|PM)"
    match = re.fullmatch(grammar, value)
    if not match:
        raise ValueError
    groups = match.groups()
    hour, minute = int(groups[0]), int(groups[1])
    second = int(groups[2]) if seconds else 0
    microsecond = int((groups[3] or "").ljust(6, "0")) if seconds and not twelve else 0
    if twelve:
        if not 1 <= hour <= 12:
            raise ValueError
        hour = hour % 12 + (12 if groups[-1] == "PM" else 0)
    return time(hour, minute, second, microsecond)


def _source_timezone(timezone, fold):
    if not isinstance(timezone, str) or not timezone or len(timezone) > 100:
        raise SourceFormatError(
            "Confirm an IANA source timezone for local timestamps.", requires_choice=True
        )
    if fold not in ("", "earlier", "later"):
        raise SourceFormatError(
            "Choose an explicit repeated-time interpretation.", requires_choice=True
        )
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SourceFormatError(
            "Choose a valid IANA source timezone.", requires_choice=True
        ) from None
    return zone


def _local_timestamp(value, timezone, fold):
    zone = _source_timezone(timezone, fold)
    instants = sorted(
        {
            candidate.astimezone(UTC)
            for flag in (0, 1)
            if (candidate := value.replace(tzinfo=zone, fold=flag))
            .astimezone(UTC)
            .astimezone(zone)
            .replace(tzinfo=None)
            == value
        }
    )
    if not instants:
        raise SourceFormatError("This local time does not exist in the source timezone (DST gap).")
    if len(instants) > 1 and not fold:
        raise SourceFormatError(
            "This local time occurs twice; choose earlier or later (DST fold).",
            requires_choice=True,
        )
    return instants[-1] if fold == "later" else instants[0]


def canonical_source(data_type, value, profile_id, *, timezone="", fold=""):
    """Parse one value with exactly the selected bounded profile."""
    profile = SOURCE_FORMATS.get(profile_id) if isinstance(profile_id, str) else None
    if profile is None or data_type not in profile.types:
        raise SourceFormatError("Choose an accepted source format for this datatype.")
    if data_type == "timestamp" and profile.kind != "canonical":
        _source_timezone(timezone, fold)
    if value is None or value == "":
        return ""
    if isinstance(value, str) and len(value) > 4096:
        raise SourceFormatError("Source value exceeds the supported length.")
    try:
        if type(value) in (int, float) and profile.id in ("v1-plain", "v1-float"):
            if data_type == "number":
                value = get_datatype("number").text(Decimal(str(value)))
            elif data_type == "integer" and type(value) is float:
                if not value.is_integer():
                    raise ValueError
                value = str(int(value))
            else:
                value = str(value)
        elif type(value) is bool and profile.kind == "boolean":
            value = "true" if value else "false"
        elif data_type == "string" and not isinstance(value, str):
            value = str(value)
        if profile.kind == "native":
            if data_type == "date":
                if type(value) is datetime and value.time() == time():
                    value = value.date()
                if type(value) is not date:
                    raise ValueError
            elif data_type == "time":
                if type(value) is not time or value.tzinfo is not None:
                    raise ValueError
            elif type(value) is not datetime or value.tzinfo is not None:
                raise ValueError
        else:
            if not isinstance(value, str):
                raise ValueError
            if profile.kind == "number":
                integer = r"[0-9]+"
                if profile.group:
                    integer = (
                        r"(?:[0-9]+|[1-9][0-9]{0,2}(?:" + re.escape(profile.group) + r"[0-9]{3})+)"
                    )
                fraction = (
                    r"(?:" + re.escape(profile.decimal) + r"[0-9]+)?"
                    if data_type == "number"
                    else ""
                )
                if not re.fullmatch("-?" + integer + fraction, value):
                    raise ValueError
                if profile.group:
                    value = value.replace(profile.group, "")
                value = value.replace(profile.decimal, ".")
            elif profile.kind == "boolean":
                value = BOOLEAN_TOKENS[value.lower()]
            elif profile.kind == "date":
                value = _date(value, profile)
            elif profile.kind == "time":
                value = _time(value, profile.time_style)
            elif profile.kind == "datetime":
                # Date width is fixed by the profile; AM/PM may contain a later space.
                width = 8 if not profile.date_separator else 10
                if value[width : width + 1] != " " and not (
                    profile.date_order == "ymd"
                    and profile.date_separator == "-"
                    and value[width : width + 1] == "T"
                ):
                    raise ValueError
                value = datetime.combine(
                    _date(value[:width], profile), _time(value[width + 1 :], profile.time_style)
                )
        if data_type == "timestamp" and profile.kind != "canonical":
            value = _local_timestamp(value, timezone, fold)
        datatype = get_datatype(data_type)
        if not isinstance(value, str):
            value = datatype.text(value)
        parsed = datatype.canonicalize(value)
        return "" if parsed is None else datatype.text(parsed[0])
    except SourceFormatError:
        raise
    except (DatatypeError, ValueError, TypeError, KeyError, OverflowError):
        raise SourceFormatError(
            "Value does not fit the selected source format or datatype limits."
        ) from None


def propose_format(data_type, values, remembered=None):
    """Full-column detection. Equivalent interpretations use catalogue order, never locale."""
    values = tuple(value for value in values if value is not None and value != "")
    if remembered:
        try:
            for value in values:
                canonical_source(data_type, value, **remembered)
            return dict(remembered), None
        except (SourceFormatError, TypeError):
            pass
    profiles = formats_for(data_type)
    if len(profiles) == 1:
        choice = {"profile_id": profiles[0].id}
        try:
            for value in values:
                canonical_source(data_type, value, **choice)
        except SourceFormatError as exc:
            return choice, str(exc)
        return choice, None
    matches = []
    for profile in profiles:
        try:
            canonical = tuple(canonical_source(data_type, value, profile.id) for value in values)
        except SourceFormatError:
            continue
        matches.append((profile, canonical))
    if not values:
        return {"profile_id": formats_for(data_type)[0].id}, None
    if matches and len({canonical for _profile, canonical in matches}) == 1:
        # Grouped integers can still be decimal-looking source text: require a decision.
        if data_type != "integer" or not any(
            isinstance(v, str) and re.fullmatch(r"-?[0-9]{1,3}[.,][0-9]{3}", v) for v in values
        ):
            return {"profile_id": matches[0][0].id}, None
    return {}, (
        "Ambiguous source format; choose how this column is interpreted."
        if matches
        else "Unknown or mixed source formats; choose one format that fits every non-empty value."
    )
