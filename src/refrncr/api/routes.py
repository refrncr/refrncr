"""Bearer-only Current JSON boundary, mounted before the unchanged Flask app."""

import re
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError as ModelValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response

from refrncr.api.contracts import (
    Collection,
    CollectionQuery,
    Dataset,
    DatasetDetail,
    Detail,
    Error,
    FieldMetadata,
    HierarchyData,
    HierarchyMetadata,
    HierarchyNode,
    HierarchyRecord,
    HierarchyRecordCollection,
    HierarchyResponse,
    Links,
    PageQuery,
    Pagination,
    Record,
    RecordCollection,
    RecordDetail,
    SelectionQuery,
    VersionMetadata,
)
from refrncr.api.documentation import CONDITIONAL_PARAMETERS, DESCRIPTION, RESPONSE_HEADERS
from refrncr.api.http import SAFE_HEADERS, CanonicalJSONResponse, ConditionalRead, utc
from refrncr.api_keys import ApiKeyAuthenticationError
from refrncr.datatypes import get_datatype
from refrncr.published_contracts import encode_identifier, record_identifier
from refrncr.services import AuthenticatedPrincipal, NotFoundError, ValidationError


def _version(publication):
    version = publication.version
    return VersionMetadata(
        number=version.number,
        published_at=get_datatype("timestamp").text(utc(version.published_at)),
        effective_from=version.effective_date.isoformat(),
    )


def _dataset(publication):
    return Dataset(
        key=publication.key,
        name=publication.name,
        description=publication.description,
        icon=publication.icon,
        kind=publication.kind,
        version=_version(publication),
    )


def _record(record, fields):
    return Record(
        identifier=_record_identifier(record, fields), values=_values(record.values, fields)
    )


def _record_identifier(record, fields):
    if not any(field.is_identifier for field in fields):
        return encode_identifier(b"hierarchy:" + record.id.encode("ascii"))
    return record_identifier(record, fields)


def _values(values, fields):
    values = {v.field_key: v.value for v in values}
    canonical = {}
    for field in fields:
        value = values.get(field.key)
        if value is not None:
            datatype = get_datatype(field.data_type)
            datatype.validate_typed(value)
            if field.data_type in {"date", "time", "datetime", "timestamp"}:
                value = datatype.text(value)
        canonical[field.key] = value
    return canonical


def _hierarchy_node(node, fields, record_identifiers):
    return HierarchyNode(
        id=node.id,
        parent_id=node.parent_id,
        name=node.name,
        values=_values(node.values, fields),
        record_identifier=record_identifiers.get(node.id),
    )


def _hierarchy_paths(nodes):
    by_id = {node.id: node for node in nodes}
    paths = {}
    for node in nodes:
        path, current, visited = [], node, set()
        while current is not None and current.id not in visited:
            visited.add(current.id)
            path.append(current)
            current = by_id.get(current.parent_id)
        paths[node.id] = tuple(reversed(path))
    return paths


def _hierarchy_record(record, fields, nodes_by_id, paths):
    node = nodes_by_id[record.id]
    path = paths[node.id]
    return HierarchyRecord(
        identifier=_record_identifier(record, fields),
        node_id=node.id,
        parent_id=node.parent_id,
        levels=tuple(item.name for item in path[:-1]),
        leaf=node.name,
        values=_values(record.values, fields),
    )


def _query(request, collection):
    pairs = list(request.query_params.multi_items())
    allowed = {"effective_date", "version"}
    if collection:
        allowed |= {"offset", "limit"}
    values = {}
    if len(request.scope.get("query_string", b"")) > 256:
        raise ValidationError("Invalid request")
    for key, value in pairs:
        if key not in allowed or key in values:
            raise ValidationError("Invalid request")
        if key == "effective_date":
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise ValidationError("Invalid request") from None
            if parsed.isoformat() != value:
                raise ValidationError("Invalid request")
            values[key] = parsed
        else:
            if re.fullmatch(r"0|[1-9][0-9]{0,9}", value) is None:
                raise ValidationError("Invalid request")
            values[key] = int(value)
    if collection:
        query = CollectionQuery(**values)
        page = query
    else:
        query = SelectionQuery(**values)
        page = PageQuery()
    return page, query.effective_date, query.version


def _pagination(page, total, path, effective_date=None, version_number=None):
    def link(offset):
        pairs = []
        if effective_date is not None:
            pairs.append("effective_date=" + effective_date.isoformat())
        if version_number is not None:
            pairs.append("version=" + str(version_number))
        pairs.extend((f"offset={offset}", f"limit={page.limit}"))
        return f"{path}?" + "&".join(pairs)

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
        title="refrncr Published data API",
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
        except Exception:  # noqa: BLE001 - the API boundary returns a generic safe response.
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
        page, effective_date, version_number = _query(request, kind in {"datasets", "records"})
        condition = ConditionalRead(
            request,
            actor_id=actor.id,
            kind=kind,
            key=key,
            identifier=identifier,
            page=page,
            effective_date=effective_date,
            version_number=version_number,
        )
        result = versions.read_current(
            actor.id,
            key,
            offset=page.offset,
            limit=page.limit,
            records=kind == "records",
            identifier=identifier,
            unchanged=condition,
            effective_date=effective_date,
            version_number=version_number,
            hierarchy=kind == "hierarchy",
            allow_hierarchy=True,
        )
        if result.not_modified:
            return Response(status_code=304, headers=condition.headers)
        if kind == "datasets":
            payload = Collection[Dataset](
                data=tuple(_dataset(p) for p in result.publications),
                **_pagination(
                    page, result.total, "/api/v1/datasets", effective_date, version_number
                ),
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
            publication = result.publications[0]
            hierarchy = None
            if publication.kind == "hierarchy":
                hierarchy = HierarchyMetadata(
                    depth=max(
                        (
                            max(len(path) - 1, 0)
                            for path in _hierarchy_paths(result.hierarchy_nodes).values()
                        ),
                        default=0,
                    )
                )
            payload = Detail[DatasetDetail](
                data=DatasetDetail(
                    **_dataset(publication).model_dump(), fields=fields, hierarchy=hierarchy
                )
            )
        elif kind == "records":
            pagination = _pagination(
                page,
                result.total,
                f"/api/v1/datasets/{key}/records",
                effective_date,
                version_number,
            )
            if result.publications[0].kind == "hierarchy":
                nodes_by_id = {node.id: node for node in result.hierarchy_nodes}
                paths = _hierarchy_paths(result.hierarchy_nodes)
                payload = HierarchyRecordCollection(
                    data=tuple(
                        _hierarchy_record(r, result.fields, nodes_by_id, paths)
                        for r in result.records
                    ),
                    version=_version(result.publications[0]),
                    **pagination,
                )
            else:
                payload = RecordCollection(
                    data=tuple(_record(r, result.fields) for r in result.records),
                    version=_version(result.publications[0]),
                    **pagination,
                )
        elif kind == "hierarchy":
            record_pairs = sorted(
                ((raw, _record(raw, result.fields)) for raw in result.records),
                key=lambda pair: pair[1].identifier,
            )
            records = tuple(record for _raw, record in record_pairs)
            record_identifiers = {raw.id: record.identifier for raw, record in record_pairs}
            payload = HierarchyResponse(
                data=HierarchyData(
                    fields=tuple(
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
                    ),
                    nodes=tuple(
                        _hierarchy_node(node, result.fields, record_identifiers)
                        for node in result.hierarchy_nodes
                    ),
                    records=records,
                ),
                version=_version(result.publications[0]),
            )
        else:
            payload = RecordDetail(
                data=_record(result.records[0], result.fields),
                version=_version(result.publications[0]),
            )
        return CanonicalJSONResponse(payload, headers=condition.headers)

    errors = {
        status: {"model": Error, "description": description}
        for status, description in {
            400: "Invalid request: unknown/duplicate query parameters or invalid pagination.",
            401: "Missing, invalid, expired or revoked API key.",
            404: "Resource absent, inaccessible, invalid, or without a selected publication.",
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
    errors[200] = {"description": "Selected published representation", "headers": RESPONSE_HEADERS}
    errors[304] = {
        "description": "Authorized representation unchanged; no response body.",
        "headers": RESPONSE_HEADERS,
    }
    conditional = {"parameters": CONDITIONAL_PARAMETERS}

    @app.get(
        "/datasets",
        response_model=Collection[Dataset],
        responses=errors,
        summary="List published Datasets",
        tags=["Published data"],
        openapi_extra=conditional,
        description="Accessible Datasets with selected publications, ordered by Dataset key.",
    )
    def datasets(
        request: Request,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
        page: Annotated[CollectionQuery, Depends()] = None,
    ):
        return read(request, actor, "datasets")

    @app.get(
        "/datasets/{dataset_key}",
        response_model=Detail[DatasetDetail],
        responses=errors,
        summary="Read a published Dataset",
        tags=["Published data"],
        openapi_extra=conditional,
        description="Selected Dataset metadata and ordered published Fields.",
    )
    def dataset(
        request: Request,
        dataset_key: str,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
        selection: Annotated[SelectionQuery, Depends()] = None,
    ):
        return read(request, actor, "dataset", dataset_key)

    @app.get(
        "/datasets/{dataset_key}/records",
        response_model=RecordCollection | HierarchyRecordCollection,
        responses=errors,
        summary="List published Records",
        tags=["Published data"],
        openapi_extra=conditional,
        description="Selected canonical values in typed identifier order; offset pagination.",
    )
    def records(
        request: Request,
        dataset_key: str,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
        page: Annotated[CollectionQuery, Depends()] = None,
    ):
        return read(request, actor, "records", dataset_key)

    @app.get(
        "/datasets/{dataset_key}/records/{identifier}",
        response_model=RecordDetail,
        responses=errors,
        summary="Read a published Record",
        tags=["Published data"],
        openapi_extra=conditional,
        description="Look up the entire opaque single/composite business identifier from a "
        "Record response. URL encode it; never substitute an internal UUID. No query parameters.",
    )
    def record(
        request: Request,
        dataset_key: str,
        identifier: str,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
        selection: Annotated[SelectionQuery, Depends()] = None,
    ):
        return read(request, actor, "record", dataset_key, identifier)

    @app.get(
        "/datasets/{dataset_key}/hierarchy",
        response_model=HierarchyResponse,
        responses=errors,
        summary="Read a published hierarchy",
        tags=["Published data"],
        openapi_extra=conditional,
        description=(
            "Read one complete, flat published hierarchy snapshot. Nodes reference their "
            "parent_id and leaves reference the corresponding opaque Record identifier."
        ),
    )
    def hierarchy(
        request: Request,
        dataset_key: str,
        actor: Annotated[AuthenticatedPrincipal, Depends(principal)],
        selection: Annotated[SelectionQuery, Depends()] = None,
    ):
        return read(request, actor, "hierarchy", dataset_key)

    generated_openapi = app.openapi

    def documented_openapi():
        schema = generated_openapi()
        # The existing boundary translates all FastAPI validation errors to safe 400s.
        # Remove FastAPI's implicit 422, which is not part of the implemented contract.
        for path in schema["paths"].values():
            operation = path["get"]
            operation["responses"].pop("422", None)
            for parameter in operation.get("parameters", []):
                if parameter["name"] in CollectionQuery.model_fields:
                    parameter["description"] = CollectionQuery.model_fields[
                        parameter["name"]
                    ].description
                    if parameter["name"] == "effective_date":
                        parameter["example"] = "2026-09-30"
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
