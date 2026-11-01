"""Additive v1 JSON contracts; no ORM, authorization or datatype parsing."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    WithJsonSchema,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SelectionQuery(Contract):
    effective_date: date | None = Field(
        default=None,
        description=(
            "Business date in ISO calendar-date format YYYY-MM-DD, with zero-padded month "
            "and day (for example 2026-09-30). Selects the latest publication effective "
            "on or before that date."
        ),
        examples=["2026-09-30"],
    )
    version: int | None = Field(
        default=None,
        ge=1,
        le=2147483647,
        description="Explicit immutable published version number.",
    )


class PageQuery(Contract):
    offset: int = Field(
        default=0,
        ge=0,
        le=2147483647,
        description="Zero-based offset; canonical ASCII whole number.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Page size; canonical ASCII whole number, maximum 100.",
    )


class CollectionQuery(PageQuery, SelectionQuery):
    pass


class HistoryQuery(PageQuery):
    valid_from: date | None = Field(
        default=None, description="Inclusive lower bound in YYYY-MM-DD format."
    )
    valid_to: date | None = Field(
        default=None, description="Exclusive upper bound in YYYY-MM-DD format."
    )


class Pagination(PageQuery):
    total: int = Field(ge=0)


class Links(Contract):
    """Application-relative pagination links; next/previous are null at boundaries."""

    self: str
    next: str | None
    previous: str | None


class SourceMetadata(Contract):
    dataset_key: str
    version: int = Field(ge=1)


class VersionMetadata(Contract):
    """Selected publication metadata with a half-open effective period."""

    number: int = Field(ge=1)
    published_at: str = Field(examples=["2000-01-01T00:00:00Z"])
    effective_from: str = Field(examples=["2000-01-01"])
    effective_to: str | None = Field(
        examples=["2001-01-01"],
        description="Exclusive end; null when no later non-cancelled version exists.",
    )
    source: SourceMetadata | None = None


class VersionListItem(VersionMetadata):
    """One published version in effective-period order."""

    status: Literal["Current", "Scheduled", "Expired"]


class Dataset(Contract):
    key: str
    name: str
    description: str
    icon: str | None
    kind: Literal["reference", "mapping", "hierarchy"]
    version: VersionMetadata


class HierarchyMetadata(Contract):
    depth: int = Field(ge=0)


class FieldMetadata(Contract):
    """Published Field metadata, in display order; definition/unit are nullable plain text."""

    key: str
    name: str
    datatype: Literal[
        "string", "integer", "number", "float", "boolean", "date", "time", "datetime", "timestamp"
    ]
    is_identifier: bool
    is_required: bool
    is_unique: bool
    display_position: int = Field(ge=1)
    definition: str | None
    unit: str | None
    mapping_role: Literal["source_identifier", "source_context", "mapping_target"] | None = None


class DatasetDetail(Dataset):
    fields: tuple[FieldMetadata, ...]
    hierarchy: HierarchyMetadata | None = None


CanonicalDecimal = Annotated[Decimal, WithJsonSchema({"type": "number"}, mode="serialization")]
CanonicalValue = StrictStr | StrictBool | StrictInt | StrictFloat | CanonicalDecimal | None


class Record(Contract):
    """Opaque business identifier and canonical values for every published Field key.

    Single/composite identifiers share the same contract. Values use native JSON types;
    temporal values are canonical strings, UTC Timestamp ends in Z, absent values are null.
    Decimal numbers retain exact precision; consumers need an exact JSON number parser.
    """

    identifier: str = Field(min_length=1, max_length=2667, pattern=r"^[A-Za-z0-9_-]+$")
    values: dict[str, CanonicalValue]


class RecordInterval(Record):
    """One unchanged visible Record state over a half-open period."""

    valid_from: str = Field(examples=["2026-01-01"])
    valid_to: str | None = Field(
        examples=["2026-07-01"], description="Exclusive end; null for an open period."
    )
    version: int = Field(ge=1, description="Version in which this state began.")


class HierarchyNode(Contract):
    id: str
    parent_id: str | None
    name: str
    values: dict[str, CanonicalValue]
    record_identifier: str | None


class HierarchyData(Contract):
    fields: tuple[FieldMetadata, ...]
    nodes: tuple[HierarchyNode, ...]
    records: tuple[Record, ...]


class Detail[T](Contract):
    data: T


class Collection[T](Contract):
    data: tuple[T, ...]
    pagination: Pagination
    links: Links


class RecordDetail(Detail[Record]):
    version: VersionMetadata


class RecordCollection(Collection[Record]):
    version: VersionMetadata


class HierarchyRecord(Contract):
    identifier: str
    node_id: str
    parent_id: str | None
    levels: tuple[str, ...]
    leaf: str
    values: dict[str, CanonicalValue]


class HierarchyRecordCollection(Collection[HierarchyRecord]):
    version: VersionMetadata


class HierarchyResponse(Contract):
    data: HierarchyData
    version: VersionMetadata


class Error(Contract):
    error: Literal[
        "invalid_api_credentials",
        "not_found",
        "invalid_request",
        "method_not_allowed",
        "service_unavailable",
    ]
