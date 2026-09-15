"""Record criteria over canonical typed values, independent of the database engine."""

from logging import Filter
from unicodedata import normalize

from refrncr.datatypes import MAX_INPUT_LENGTH, DatatypeError, get_datatype

MAX_QUERY_BYTES = 32 * 1024


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
        if path.startswith("/datasets/") and path.rstrip("/").endswith("/records"):
            record.args = (client, method, path, version, status)
        return True


def search_term(raw):
    if not isinstance(raw, str) or len(raw) > 200:
        raise RecordSearchError("Search must not exceed 200 characters.")
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
        "import_results",
        "status_selection",
        "action",
    }
    for name, value in items:
        if not isinstance(value, str) or len(value) > MAX_INPUT_LENGTH:
            raise RecordSearchError("Record criteria exceed the input limit.")
        if name == "status":
            if value not in {"Added", "Changed", "Deleted"} or value in statuses:
                raise RecordSearchError("Choose distinct valid import statuses.")
            statuses.append(value)
        elif name not in allowed or name in values:
            raise RecordSearchError("Unknown, duplicate or over-limit Record criteria.")
        else:
            values[name] = value
    for name in ("import_results", "status_selection"):
        if name in values and values[name] != "1":
            raise RecordSearchError("Invalid import results selection.")
    if statuses and values.get("import_results") != "1":
        raise RecordSearchError("Import statuses require import results.")
    if "status_selection" in values and "import_results" not in values:
        raise RecordSearchError("Import statuses require import results.")
    if values.get("action", "") not in {"", "search", "apply"}:
        raise RecordSearchError("Invalid search action.")
    status = values.get("record_status", "all" if values.get("import_results") else "active")
    if status not in {"active", "deleted", "all"}:
        raise RecordSearchError("Choose Active, Deleted or All.")
    page = values.get("page", "1")
    if not page.isascii() or not page.isdecimal() or page.startswith("0") or len(page) > 9:
        raise RecordSearchError("Page must be a canonical positive integer.")
    return values, tuple(statuses), status, int(page)
