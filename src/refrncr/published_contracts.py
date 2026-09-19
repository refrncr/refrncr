"""Bounded Current-read results and transport encoding of the existing business key."""

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime

from refrncr.datatypes import DATATYPES, DatatypeError
from refrncr.ports import StoredRecord
from refrncr.version_contracts import PublishedVersion, SnapshotField


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
        if len(key) > 2000 or encode_identifier(key) != identifier:
            raise ValueError
        remaining = key
        while remaining:
            if len(remaining) < 5:
                raise ValueError
            length = int.from_bytes(remaining[:4], "big")
            component, remaining = remaining[4 : 4 + length], remaining[4 + length :]
            if length < 2 or len(component) != length:
                raise ValueError
            datatype = next(d for d in DATATYPES.values() if d.key_prefix == component[:1])
            if not datatype.identifier_eligible:
                raise ValueError
            datatype.from_key(component)
        return key
    except (ValueError, StopIteration, binascii.Error, DatatypeError):
        raise ValueError("Invalid identifier") from None


def record_identifier(record, fields):
    values = {value.field_key: value.canonical_key for value in record.values}
    return encode_identifier(
        b"".join(
            len(values[f.key]).to_bytes(4, "big") + values[f.key]
            for f in sorted(fields, key=lambda f: f.key)
            if f.is_identifier
        )
    )


@dataclass(frozen=True, slots=True)
class CurrentPublication:
    key: str
    name: str
    description: str
    icon: str | None
    metadata_updated_at: datetime
    version: PublishedVersion


@dataclass(frozen=True, slots=True)
class CurrentRead:
    publications: tuple[CurrentPublication, ...]
    total: int
    fields: tuple[SnapshotField, ...] = ()
    records: tuple[StoredRecord, ...] = ()
    not_modified: bool = False
