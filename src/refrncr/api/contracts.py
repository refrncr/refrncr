"""Additive v1 JSON contracts; no ORM, authorization or datatype parsing."""

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


class PageQuery(Contract):
    offset: int = Field(default=0, ge=0, le=2147483647)
    limit: int = Field(default=50, ge=1, le=100)


class Pagination(PageQuery):
    total: int = Field(ge=0)


class Links(Contract):
    self: str
    next: str | None
    previous: str | None


class CurrentVersion(Contract):
    number: int = Field(ge=1)
    published_at: str


class Dataset(Contract):
    key: str
    name: str
    description: str
    icon: str | None
    current_version: CurrentVersion


class FieldMetadata(Contract):
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


class DatasetDetail(Dataset):
    fields: tuple[FieldMetadata, ...]


CanonicalDecimal = Annotated[Decimal, WithJsonSchema({"type": "number"}, mode="serialization")]
CanonicalValue = StrictStr | StrictBool | StrictInt | StrictFloat | CanonicalDecimal | None


class Record(Contract):
    identifier: str = Field(min_length=1, max_length=2667, pattern=r"^[A-Za-z0-9_-]+$")
    values: dict[str, CanonicalValue]


class Detail[T](Contract):
    data: T


class Collection[T](Contract):
    data: tuple[T, ...]
    pagination: Pagination
    links: Links


class RecordDetail(Detail[Record]):
    current_version: CurrentVersion


class RecordCollection(Collection[Record]):
    current_version: CurrentVersion


class Error(Contract):
    error: Literal[
        "invalid_api_credentials",
        "not_found",
        "invalid_request",
        "method_not_allowed",
        "service_unavailable",
    ]
