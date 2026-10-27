"""Public documentation composed from the registered FastAPI routes."""

from copy import deepcopy
from importlib.resources import files

from fastapi.openapi.utils import get_openapi
from starlette.responses import HTMLResponse

from refrncr.api.http import SAFE_HEADERS, CanonicalJSONResponse

DESCRIPTION = """
Read-only access to **published data** using exactly one
`Authorization: Bearer <api-key>` header. Browser cookies and sessions do not authenticate
this API. Use Authorize to enter the key alone; it is retained only in this page's memory.
Missing, invalid, expired or revoked keys return 401. Missing and inaccessible resources
both return 404. All data responses are JSON; 304 has no body.

Keys use their owner's current dataset export rights. The installation's Dataset access
policy defaults to Everyone, allowing every active user to read published datasets.
In either stricter mode, administrators, owning-team members and explicitly granted users
or teams can read them. Dataset lists contain only authorized datasets; other datasets
return 404. Revoked grants, changed team membership or ownership, and policy changes apply
to the next request. An in-flight consistent read may finish. This dataset-level check
does not change version selection: Scheduled publications remain available by version or
future effective date, even though file export does not offer them.

All operations accept optional `effective_date=YYYY-MM-DD` and `version=<number>` selectors.
The former chooses the latest publication effective on or before that business date; the
latter chooses an immutable published version. When combined, that version must be effective
on the date. Collections additionally accept `offset` and `limit`, in canonical ASCII
whole-number notation
(no signs, leading zeroes except 0, fractions or exponents). Duplicate/unknown parameters
and querystrings over 256 bytes return 400.
Dataset order is key ascending; Records use canonical typed business-identifier order.
Links are application-relative; next/previous may be null. Separate pages are not a pinned
snapshot: check selected-version metadata when traversing pages.

Record values are canonical, keyed by Field key: String and Boolean are native JSON;
Integer, Decimal (`number`) and Float are JSON numbers. Use an exact JSON number parser
for large integers and decimals. Date, Time and DateTime are canonical strings;
Timestamp is UTC with `Z`. Missing values are null. No locale/display formatting applies.
Identifiers are opaque business identifiers, never internal Record UUIDs. Single and
composite identifiers share the same contract: URL encode the **entire returned identifier**;
do not reconstruct it from display values. Encoding is reversible, not encryption.

The hierarchy endpoint returns one complete published snapshot at
`/api/v1/datasets/{dataset_key}/hierarchy`. Its flat `nodes` contain stable node IDs,
`parent_id`, names, canonical values and a `record_identifier` for leaves; `records`
contains the corresponding leaf records. Intermediate branches are not records. The
snapshot is not paginated so it can be reconstructed independently. Hierarchy selection
uses the same version/effective-date, authorization, error, ETag and cache rules as the
other published-data endpoints.

For mappings, `/api/v1/datasets/{dataset_key}/lookup?source=1001` looks up one source
value, for example `1001 → AMS-SALES`. This is a targeted lookup, not general filtering.
The value is parsed using the selected version's `source_identifier` Field datatype.
Invalid values return 400; unknown values and non-mapping datasets return 404. Unmapped
source values return 200 with the Record and null target values. The response is the same
RecordDetail as `/records/{identifier}`, with the same version selectors and caching rules.
Field metadata includes `mapping_role`: `source_identifier`, `source_context`, or
`mapping_target`; it is null outside mappings. Version metadata includes `source` with
`dataset_key` and `version` for mappings, otherwise null. The source key and version are
visible to anyone with export access to the mapping; source dataset access is not required.

Every successful data response supplies a strong ETag and publication-based Last-Modified.
Send If-None-Match to revalidate; matching tags (including weak/list matches and `*`)
return 304 only after authentication, authorization and existence checks. ETags also track
current Dataset metadata and the selected page. If-None-Match takes precedence over
If-Modified-Since. Date-only validation is conservative and may return 200 even when
Last-Modified is unchanged. Dataset lists use the page's latest publication time, or Unix
epoch for an empty page. Private caches must revalidate every use; shared caching is forbidden.

**Excluded:** Concepts, Cancelled versions, history listing, search/filtering/sorting, writes,
import/export, reverse mapping lookup and batch lookup. The hierarchy endpoint is read-only
and does not expose concept data, internal IDs as record identifiers, cross-dataset references
or aggregations.
""".strip()

CONDITIONAL_PARAMETERS = [
    {
        "name": "If-None-Match",
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": "Previous ETag, list of tags or *. Authoritative over If-Modified-Since.",
    },
    {
        "name": "If-Modified-Since",
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": "Optional HTTP date. Conservative secondary validator; prefer ETags.",
    },
]

MAPPING_LOOKUP_EXAMPLE = {
    "data": {
        "identifier": "AAAABWkxMDAx",
        "values": {"code": 1001, "target": "AMS-SALES"},
    },
    "version": {
        "number": 1,
        "published_at": "2026-10-27T12:00:00Z",
        "effective_from": "2026-10-27",
        "source": {"dataset_key": "sap-cost-centres", "version": 1},
    },
}

RESPONSE_HEADERS = {
    "ETag": {"schema": {"type": "string"}, "description": "Strong representation validator."},
    "Last-Modified": {
        "schema": {"type": "string"},
        "description": "Selected publication time as HTTP date (page maximum; epoch if empty).",
    },
    "Cache-Control": {
        "schema": {"type": "string"},
        "description": "private, no-cache, max-age=0, must-revalidate",
    },
    "Vary": {"schema": {"type": "string"}, "description": "Authorization, Accept-Encoding"},
}


def publish_documentation(app, api):
    """Register only the two public documentation endpoints, before the Flask mount."""
    html = files("refrncr").joinpath("templates/pages/api/docs.html").read_text()
    headers = {
        **SAFE_HEADERS,
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        ),
    }

    @app.get(
        "/api/openapi.json",
        response_class=CanonicalJSONResponse,
        summary="Read the public OpenAPI document",
        description="Generated contract only; no Dataset, Record, user or credential data.",
        tags=["Documentation"],
    )
    def openapi_document():
        try:
            return CanonicalJSONResponse(app.openapi(), headers=SAFE_HEADERS)
        except Exception:  # noqa: BLE001 - the public schema endpoint must fail closed.
            return CanonicalJSONResponse(
                {"error": "service_unavailable"}, status_code=503, headers=SAFE_HEADERS
            )

    @app.get(
        "/api/docs",
        response_class=HTMLResponse,
        summary="Open the public Swagger UI",
        description="Interactive documentation; raw OpenAPI JSON is the accessible alternative.",
        tags=["Documentation"],
    )
    def swagger_ui():
        return HTMLResponse(html, headers=headers)

    def schema():
        if app.openapi_schema is None:
            document = deepcopy(api.openapi())
            # A mounted app's paths are relative. Publish the actual public paths, without
            # deriving servers from untrusted Host/forwarded headers or request state.
            document["paths"] = {"/api/v1" + p: item for p, item in document["paths"].items()}
            docs = get_openapi(title=api.title, version=api.version, routes=app.routes)
            document["paths"].update(docs["paths"])
            document.pop("servers", None)
            app.openapi_schema = document
        return app.openapi_schema

    app.openapi = schema
