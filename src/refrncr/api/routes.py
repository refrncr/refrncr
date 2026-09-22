"""Bearer-only Current JSON boundary, mounted before the unchanged Flask app."""

import re
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError as ModelValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response

from refrncr.api.contracts import (
    Collection,
    CurrentVersion,
    Dataset,
    DatasetDetail,
    Detail,
    Error,
    FieldMetadata,
    Links,
    PageQuery,
    Pagination,
    Record,
    RecordCollection,
    RecordDetail,
)
from refrncr.api.documentation import CONDITIONAL_PARAMETERS, DESCRIPTION, RESPONSE_HEADERS
from refrncr.api.http import SAFE_HEADERS, CanonicalJSONResponse, ConditionalRead, utc
from refrncr.api_keys import ApiKeyAuthenticationError
from refrncr.datatypes import get_datatype
from refrncr.published_contracts import record_identifier
from refrncr.services import AuthenticatedPrincipal, NotFoundError, ValidationError


def _version(publication):
    version = publication.version
    return CurrentVersion(
        number=version.number,
        published_at=get_datatype("timestamp").text(utc(version.published_at)),
    )


def _dataset(publication):
    return Dataset(
        key=publication.key,
        name=publication.name,
        description=publication.description,
        icon=publication.icon,
        current_version=_version(publication),
    )


def _record(record, fields):
    values = {v.field_key: v.value for v in record.values}
    canonical = {}
    for field in fields:
        value = values.get(field.key)
        if value is not None:
            datatype = get_datatype(field.data_type)
            datatype.validate_typed(value)
            if field.data_type in {"date", "time", "datetime", "timestamp"}:
                value = datatype.text(value)
        canonical[field.key] = value
    return Record(identifier=record_identifier(record, fields), values=canonical)


def _page(request, collection):
    pairs = list(request.query_params.multi_items())
    allowed = {"offset", "limit"} if collection else set()
    values = {}
    if len(request.scope.get("query_string", b"")) > 256:
        raise ValidationError("Invalid request")
    for key, value in pairs:
        if key not in allowed or key in values or re.fullmatch(r"0|[1-9][0-9]{0,9}", value) is None:
            raise ValidationError("Invalid request")
        values[key] = int(value)
    return PageQuery(**values)


def _pagination(page, total, path):
    def link(offset):
        return f"{path}?offset={offset}&limit={page.limit}"

    return dict(
        pagination=Pagination(offset=page.offset, limit=page.limit, total=total),
        links=Links(
            self=link(page.offset),
            next=link(page.offset + page.limit) if page.offset + page.limit < total else None,
            previous=link(max(0, page.offset - page.limit)) if page.offset else None,
        ),
    )


def create_api(versions, api_keys):
    app = FastAPI(
        title="refrncr Current data API",
        version="1",
        description=DESCRIPTION,
        root_path_in_servers=False,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
        default_response_class=CanonicalJSONResponse,
    )
    bearer = HTTPBearer(
        auto_error=False,
        description="API key in Authorization: Bearer <api-key>. Enter the key alone. "
        "Cookies and browser sessions are not accepted.",
    )

    def principal(
        request: Request,
        _credential: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ):
        values = request.headers.getlist("authorization")
        if api_keys is None:
            raise ApiKeyAuthenticationError()
        # Malformed/duplicate headers use the same service failure audit as absent keys.
        authenticated = api_keys.authenticate(values[0] if len(values) == 1 else None)
        request.scope["refrncr.authentication"] = "api_key"
        return authenticated

    @app.middleware("http")
    async def safety(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            # Never forward exception text/SQL/request state to ASGI exception logging.
            response = CanonicalJSONResponse(Error(error="service_unavailable"), status_code=503)
        for name, value in SAFE_HEADERS.items():
            if name.lower() not in response.headers:
                response.headers[name] = value
        return response

    @app.exception_handler(ApiKeyAuthenticationError)
    async def credentials_error(request, error):
        return CanonicalJSONResponse(
            Error(error="invalid_api_credentials"),
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(NotFoundError)
    async def not_found(request, error):
        return CanonicalJSONResponse(Error(error="not_found"), status_code=404)

    @app.exception_handler(ValidationError)
    @app.exception_handler(ModelValidationError)
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return CanonicalJSONResponse(Error(error="invalid_request"), status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        code = "method_not_allowed" if error.status_code == 405 else "not_found"
        return CanonicalJSONResponse(Error(error=code), status_code=error.status_code)

    def read(request, actor, kind, key=None, identifier=None):
        page = _page(request, kind in {"datasets", "records"})
        condition = ConditionalRead(
            request, actor_id=actor.id, kind=kind, key=key, identifier=identifier, page=page
        )
        result = versions.read_current(
            actor.id,
            key,
            offset=page.offset,
            limit=page.limit,
            records=kind == "records",
            identifier=identifier,
            unchanged=condition,
        )
        if result.not_modified:
            return Response(status_code=304, headers=condition.headers)
        if kind == "datasets":
            payload = Collection[Dataset](
                data=tuple(_dataset(p) for p in result.publications),
                **_pagination(page, result.total, "/api/v1/datasets"),
            )
        elif kind == "dataset":
            fields = tuple(
                FieldMetadata(
                    key=f.key,
                    name=f.name,
                    datatype=f.data_type,
                    is_identifier=f.is_identifier,
                    is_required=f.is_required,
                    is_unique=f.is_unique,
                    display_position=f.display_position,
                    definition=f.definition,
                    unit=f.unit,
                )
                for f in result.fields
            )
            payload = Detail[DatasetDetail](
                data=DatasetDetail(**_dataset(result.publications[0]).model_dump(), fields=fields)
            )
        elif kind == "records":
            payload = RecordCollection(
                data=tuple(_record(r, result.fields) for r in result.records),
                current_version=_version(result.publications[0]),
                **_pagination(page, result.total, f"/api/v1/datasets/{key}/records"),
            )
        else:
            payload = RecordDetail(
                data=_record(result.records[0], result.fields),
                current_version=_version(result.publications[0]),
            )
        return CanonicalJSONResponse(payload, headers=condition.headers)

    errors = {
        status: {"model": Error, "description": description}
        for status, description in {
            400: "Invalid request: unknown/duplicate query parameters or invalid pagination.",
            401: "Missing, invalid, expired or revoked API key.",
            404: "Resource absent, inaccessible, invalid, or without a Current publication.",
            405: "Method not allowed; the API is read-only.",
            503: "Temporarily unavailable. No internal error details are returned.",
        }.items()
    }
    for status, code in {
        400: "invalid_request",
        401: "invalid_api_credentials",
        404: "not_found",
        405: "method_not_allowed",
        503: "service_unavailable",
    }.items():
        errors[status]["content"] = {
            "application/json": {"example": Error(error=code).model_dump()}
        }
    errors[401]["headers"] = {
        "WWW-Authenticate": {"schema": {"type": "string"}, "description": "Bearer"}
    }
    errors[200] = {"description": "Current published representation", "headers": RESPONSE_HEADERS}
    errors[304] = {
        "description": "Authorized representation unchanged; no response body.",
        "headers": RESPONSE_HEADERS,
    }
    conditional = {"parameters": CONDITIONAL_PARAMETERS}

    @app.get(
        "/datasets",
        response_model=Collection[Dataset],
        responses=errors,
        summary="List Current Datasets",
        tags=["Current data"],
        openapi_extra=conditional,
        description="Accessible Datasets with Current publications only, ordered by Dataset key.",
    )
    def datasets(
        request: Request,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
        page: Annotated[PageQuery, Depends()] = None,
    ):
        return read(request, actor, "datasets")

    @app.get(
        "/datasets/{dataset_key}",
        response_model=Detail[DatasetDetail],
        responses=errors,
        summary="Read a Current Dataset",
        tags=["Current data"],
        openapi_extra=conditional,
        description="Current Dataset metadata and ordered published Fields. No query parameters.",
    )
    def dataset(
        request: Request,
        dataset_key: str,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
    ):
        return read(request, actor, "dataset", dataset_key)

    @app.get(
        "/datasets/{dataset_key}/records",
        response_model=RecordCollection,
        responses=errors,
        summary="List Current Records",
        tags=["Current data"],
        openapi_extra=conditional,
        description="Current canonical values in typed identifier order; offset pagination.",
    )
    def records(
        request: Request,
        dataset_key: str,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
        page: Annotated[PageQuery, Depends()] = None,
    ):
        return read(request, actor, "records", dataset_key)

    @app.get(
        "/datasets/{dataset_key}/records/{identifier}",
        response_model=RecordDetail,
        responses=errors,
        summary="Read a Current Record",
        tags=["Current data"],
        openapi_extra=conditional,
        description="Look up the entire opaque single/composite business identifier from a "
        "Record response. URL encode it; never substitute an internal UUID. No query parameters.",
    )
    def record(
        request: Request,
        dataset_key: str,
        identifier: str,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
    ):
        return read(request, actor, "record", dataset_key, identifier)

    generated_openapi = app.openapi

    def documented_openapi():
        schema = generated_openapi()
        # The existing boundary translates all FastAPI validation errors to safe 400s.
        # Remove FastAPI's implicit 422, which is not part of the implemented contract.
        for path in schema["paths"].values():
            operation = path["get"]
            operation["responses"].pop("422", None)
            for parameter in operation.get("parameters", []):
                if parameter["name"] in PageQuery.model_fields:
                    parameter["description"] = PageQuery.model_fields[parameter["name"]].description
                elif parameter["name"] == "dataset_key":
                    parameter["description"] = (
                        "Permanent Dataset key from the Dataset list: 1–100 lowercase ASCII "
                        "letters/digits, underscore or hyphen; starts with a letter/digit. "
                        "Invalid, missing and "
                        "inaccessible keys return 404."
                    )
                elif parameter["name"] == "identifier":
                    parameter["schema"] = Record.model_json_schema()["properties"]["identifier"]
                    parameter["description"] = (
                        "Entire opaque business identifier from a Record response, URL encoded. "
                        "Single and composite identifiers use the same contract; invalid or "
                        "missing identifiers return 404."
                    )
        for name in ("HTTPValidationError", "ValidationError"):
            schema["components"]["schemas"].pop(name, None)
        return schema

    app.openapi = documented_openapi
    return app
