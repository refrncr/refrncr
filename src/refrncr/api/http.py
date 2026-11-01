"""Safe JSON and HTTP conditional retrieval, separate from domain reads."""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import format_datetime, parsedate_to_datetime

from pydantic import BaseModel
from starlette.responses import JSONResponse

from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.datatypes import get_datatype

PRIVATE = "private, no-cache, max-age=0, must-revalidate"
SAFE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Vary": "Authorization, Accept-Encoding",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def json_bytes(value):
    """Exact Decimal JSON number tokens; all other escaping uses the stdlib encoder."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Decimal):
        return get_datatype("number").text(value).encode("ascii")
    if isinstance(value, dict):
        return (
            b"{" + b",".join(json_bytes(k) + b":" + json_bytes(v) for k, v in value.items()) + b"}"
        )
    if isinstance(value, tuple | list):
        return b"[" + b",".join(json_bytes(v) for v in value) + b"]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )


class CanonicalJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content):
        return json_bytes(content)


class ConditionalRead:
    def __init__(
        self,
        request,
        *,
        actor_id,
        kind,
        key,
        identifier,
        page,
        effective_date,
        version_number,
        clock: Clock = SYSTEM_CLOCK,
    ):
        self.request = request
        self.clock = clock
        # The additive metadata changes response bytes; pre-metadata ETags must revalidate.
        self.selection = (
            "refrncr-json-v1-mapping-metadata",
            actor_id,
            kind,
            key,
            identifier,
            page.offset,
            page.limit,
            effective_date.isoformat() if effective_date is not None else None,
            version_number,
        )
        self.headers = {}

    def __call__(self, result):
        publications = result.publications
        signature = [
            (
                p.key,
                p.name,
                p.description,
                p.icon,
                p.kind,
                p.version.id,
                p.version.number,
                p.version.published_at.isoformat(),
                p.version.effective_date.isoformat(),
                p.valid_until.isoformat() if p.valid_until else None,
                (p.source.dataset_key, p.source.version) if p.source else None,
            )
            + ((p.status,) if p.status is not None else ())
            for p in publications
        ]
        etag = (
            '"'
            + hashlib.sha256(json_bytes((self.selection, result.total, signature))).hexdigest()
            + '"'
        )
        modified = max(
            (p.version.published_at for p in publications),
            default=datetime(1970, 1, 1, tzinfo=UTC),
        )
        self.headers = {
            "ETag": etag,
            "Last-Modified": format_datetime(modified.replace(microsecond=0), usegmt=True),
            "Cache-Control": PRIVATE,
        }
        values = self.request.headers.getlist("if-none-match")
        if values:
            raw = ",".join(values)
            if len(raw) > 8192:
                return False
            if raw.strip() == "*":
                return True
            # A malformed list is ignored in full; commas inside opaque tags are legal.
            if (
                re.fullmatch(
                    r'\s*(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"(?:\s*,\s*(?:W/)?"[\x21\x23-\x7e\x80-\xff]*")*\s*',
                    raw,
                )
                is None
            ):
                return False
            return etag in re.findall(r'(?:W/)?("[\x21\x23-\x7e\x80-\xff]*?")', raw)
        values = self.request.headers.getlist("if-modified-since")
        if len(values) != 1 or len(values[0]) > 128:
            return False
        try:
            since = parsedate_to_datetime(values[0])
        except (TypeError, ValueError, OverflowError):
            return False
        if since.tzinfo is None:
            return False
        # Collection deletion/page membership and independently mutable metadata cannot
        # be proven unchanged by a publication date. ETags cover both exactly.
        if self.selection[2] in {"datasets", "versions"} or any(
            p.metadata_updated_at > since
            or datetime.combine(p.version.effective_date, datetime.min.time(), UTC) > since
            for p in publications
        ):
            return False
        # Compare actual precision: same-second publications must never produce a false 304.
        return modified <= since <= self.clock.now()
