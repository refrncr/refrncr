"""Record criteria over canonical typed values, independent of the database engine."""

from logging import Filter
from unicodedata import normalize

from refrncr.change_status import LEGACY_STATUS_ALIASES, RECORD_STATUS_FILTERS
from refrncr.datatypes import MAX_INPUT_LENGTH, DatatypeError, get_datatype
from refrncr.diff_contracts import MAX_SEARCH
from refrncr.presentation import DISPLAY_STATUSES

MAX_QUERY_BYTES = 32 * 1024
RECORD_CHANGE_FILTERS = tuple(RECORD_STATUS_FILTERS)


class RecordSearchError(ValueError):
    """Safe fixed messages; never attach submitted values or Field names."""


class RecordQueryLogFilter(Filter):
    """Redact Record query strings in Uvicorn's five-argument access events."""

    def filter(self, record):
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return False
        client, method, target, version, status = record.args
        if not isinstance(target, str):
            return False
        path = target.partition("?")[0]
        from urllib.parse import unquote

        if unquote(path).startswith("/api/"):
            record.args = (client, method, "/api/[redacted]", version, status)
            return True
        if path.startswith("/datasets/") and path.rstrip("/").endswith("/records"):
            record.args = (client, method, path, version, status)
        return True


def search_term(raw):
    if not isinstance(raw, str) or len(raw) > MAX_SEARCH:
        raise RecordSearchError(f"Search must not exceed {MAX_SEARCH} characters.")
    try:
        parsed = get_datatype("string").canonicalize(raw)
    except DatatypeError:
        raise RecordSearchError("Enter a valid search term.") from None
    return parsed[0] if parsed else ""


def matches(record, fields, term):
    # A deleted/recreated Field with the same key is a different Field. Retained
    # values with an old datatype must not be reinterpreted as the displayed type.
    values = {(value.field_id, value.data_type): value.value for value in record.values}
    if term and not any(
        term in normalize("NFC", value).casefold()
        for field in fields
        if field.data_type == "string"
        and (value := values.get((field.id, field.data_type))) is not None
    ):
        return False
    return True


def parse_parameters(items):
    """Strict GET envelope, also used by native form submissions (no JSON parser)."""
    values, statuses = {}, []
    allowed = {
        "q",
        "page",
        "record_status",
        "mapping_status",
        "notice",
        "import_results",
        "status_selection",
        "action",
        "focus_record",
        "focus_mode",
    }
    for name, value in items:
        if not isinstance(value, str) or len(value) > MAX_INPUT_LENGTH:
            raise RecordSearchError("Record criteria exceed the input limit.")
        if name == "status":
            value = LEGACY_STATUS_ALIASES.get(value, value)
            if value not in DISPLAY_STATUSES or value in statuses:
                raise RecordSearchError("Choose distinct valid import statuses.")
            statuses.append(value)
        elif name not in allowed or name in values:
            raise RecordSearchError("Unknown, duplicate or over-limit Record criteria.")
        else:
            values[name] = value
    if "notice" in values and values["notice"] != "source-updated":
        raise RecordSearchError("Invalid Record notice.")
    for name in ("import_results", "status_selection"):
        if name in values and values[name] != "1":
            raise RecordSearchError("Invalid import results selection.")
    if statuses and values.get("import_results") != "1":
        raise RecordSearchError("Import statuses require import results.")
    if "status_selection" in values and "import_results" not in values:
        raise RecordSearchError("Import statuses require import results.")
    if values.get("action", "") not in {"", "search", "apply"}:
        raise RecordSearchError("Invalid search action.")
    if "focus_record" in values:
        if not values["focus_record"] or values.get("focus_mode") not in {"pointer", "keyboard"}:
            raise RecordSearchError("Invalid focus return parameters.")
    elif "focus_mode" in values:
        raise RecordSearchError("Invalid focus return parameters.")
    status = values.get("record_status", "all")
    selected_changes = tuple(status.split(",")) if status != "all" else ()
    if (
        not selected_changes
        and status != "all"
        or any(change not in RECORD_CHANGE_FILTERS for change in selected_changes)
        or tuple(change for change in RECORD_CHANGE_FILTERS if change in selected_changes)
        != selected_changes
    ):
        raise RecordSearchError("Choose Added, Changed or Deleted filters.")
    page = values.get("page", "1")
    if not page.isascii() or not page.isdecimal() or page.startswith("0") or len(page) > 9:
        raise RecordSearchError("Page must be a canonical positive integer.")
    return values, tuple(statuses), status, int(page)
