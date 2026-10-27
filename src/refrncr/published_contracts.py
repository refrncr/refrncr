"""Bounded Current-read results and transport encoding of the existing business key."""

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime

from refrncr.datatypes import DATATYPES, DatatypeError, get_datatype
from refrncr.hierarchy_contracts import HierarchyNode
from refrncr.ports import StoredRecord
from refrncr.record_identity import (
    MAX_RECORD_IDENTIFIER_BYTES,
    decode_record_identifier,
    encode_record_identifier,
)
from refrncr.version_contracts import PublishedVersion, SnapshotField, SourceVersion


def encode_identifier(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii")


def decode_identifier(identifier: str) -> bytes:
    """Decode the complete existing length-prefixed key; never parse a display label."""
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 2667:
        raise ValueError("Invalid identifier")
    if re.fullmatch(r"[A-Za-z0-9_-]+", identifier) is None:
        raise ValueError("Invalid identifier")
    try:
        key = base64.b64decode(
            identifier + "=" * (-len(identifier) % 4), altchars=b"-_", validate=True
        )
        if len(key) > MAX_RECORD_IDENTIFIER_BYTES or encode_identifier(key) != identifier:
            raise ValueError
        for component in decode_record_identifier(key):
            if len(component) < 2:
                raise ValueError
            datatype = next(d for d in DATATYPES.values() if d.key_prefix == component[:1])
            if not datatype.identifier_eligible:
                raise ValueError
            datatype.from_key(component)
        return key
    except (ValueError, StopIteration, binascii.Error, DatatypeError):
        raise ValueError("Invalid identifier") from None


def mapping_identifier(source, fields):
    """Encode a source value using the selected publication's source Field datatype."""
    field = next(field for field in fields if field.mapping_role == "source_identifier")
    canonical = get_datatype(field.data_type).canonicalize(source)
    if canonical is None:
        raise DatatypeError("Source identifier is required")
    return encode_identifier(encode_record_identifier({field.key: canonical[1]}))


def record_identifier(record, fields):
    values = {value.field_key: value.canonical_key for value in record.values}
    return encode_identifier(
        encode_record_identifier(
            {field.key: values.get(field.key) for field in fields if field.is_identifier},
            expected_keys=(field.key for field in fields if field.is_identifier),
        )
    )


@dataclass(frozen=True, slots=True)
class CurrentPublication:
    key: str
    name: str
    description: str
    icon: str | None
    kind: str
    metadata_updated_at: datetime
    version: PublishedVersion
    source: SourceVersion | None = None


@dataclass(frozen=True, slots=True)
class CurrentRead:
    publications: tuple[CurrentPublication, ...]
    total: int
    fields: tuple[SnapshotField, ...] = ()
    records: tuple[StoredRecord, ...] = ()
    hierarchy_nodes: tuple[HierarchyNode, ...] = ()
    not_modified: bool = False
