"""Administrator-only audit reads, bounded filters and authenticated cursors."""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from refrncr.audit_presentation import (
    ACTION_LABELS,
    DELETED_ACTOR,
    OBJECTS,
    AuditView,
    event_deletes_object,
    present_event,
    safe_name,
    valid_id,
)
from refrncr.ports import (
    AUDIT_NAMED_OBJECT_TYPES,
    AuditFilters,
    AuditObjectRecord,
    AuditPosition,
    AuditReadUnavailable,
    UnitOfWorkFactory,
)
from refrncr.services import AuthorizationError, NotFoundError, ValidationError, require_admin

PAGE_SIZE = 50
CURSOR_LIFETIME = 12 * 60 * 60
FILTER_KEYS = frozenset({"start", "end", "actor", "action", "object_type"})
INVALID_FILTERS = "Invalid audit filters. Check the dates and selections, or clear filters."
INVALID_CURSOR = "This audit page link is invalid or expired. Clear filters to start again."


def parse_audit_time(value):
    # Explicit offset required; never interpret an ambiguous server-local timestamp.
    if (
        not isinstance(value, str)
        or len(value) > 40
        or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})", value
        )
    ):
        raise ValidationError(INVALID_FILTERS)
    try:
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError
        return datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValidationError(INVALID_FILTERS) from None


def filter_values(filters):
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key in sorted(FILTER_KEYS)
        if (value := getattr(filters, key))
    }


def parse_audit_filters(pairs):
    """The web boundary supplies all query pairs, preserving duplicates for rejection."""
    values = {}
    for key, value in pairs:
        if key not in FILTER_KEYS | {"cursor"} or key in values:
            raise ValidationError(INVALID_FILTERS)
        if not isinstance(value, str) or len(value) > (2048 if key == "cursor" else 140):
            raise ValidationError(INVALID_FILTERS)
        values[key] = value
    cursor = values.pop("cursor", None)
    if cursor == "":
        raise ValidationError(INVALID_CURSOR)
    dates = {key: parse_audit_time(values[key]) for key in ("start", "end") if values.get(key)}
    filters = AuditFilters(**{k: v for k, v in values.items() if v} | dates)
    validate_filters(filters)
    return filters, cursor


def validate_filters(filters):
    if not isinstance(filters, AuditFilters):
        raise ValidationError(INVALID_FILTERS)
    for value in (filters.start, filters.end):
        if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
            raise ValidationError(INVALID_FILTERS)
    if filters.start and filters.end and filters.start > filters.end:
        raise ValidationError(INVALID_FILTERS)
    if not isinstance(filters.actor, str) or (filters.actor and not valid_id(filters.actor)):
        raise ValidationError(INVALID_FILTERS)
    if not isinstance(filters.object_type, str) or (
        filters.object_type and filters.object_type not in OBJECTS
    ):
        raise ValidationError(INVALID_FILTERS)
    if not isinstance(filters.action, str) or (
        filters.action
        and filters.action not in {f"{kind}:{action}" for kind, action in ACTION_LABELS}
    ):
        raise ValidationError(INVALID_FILTERS)


def position(event):
    timestamp = event.timestamp
    return AuditPosition(
        timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC),
        event.id,
    )


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: tuple[AuditView, ...]
    next_cursor: str | None
    previous_cursor: str | None
    actors: tuple[tuple[str, str], ...]


class AuditHistoryService:
    def __init__(self, uow: UnitOfWorkFactory, *, cursor_key=None, clock=None):
        self.uow = uow
        self._key = cursor_key if cursor_key is not None else secrets.token_bytes(32)
        if not isinstance(self._key, bytes) or len(self._key) < 32:
            raise ValueError("Audit cursor signing key requires at least 32 bytes")
        self._clock = clock or (lambda: datetime.now(UTC))

    def list_history(self, actor_id, filters=None, cursor=None):
        with self.uow() as work:
            self._authorize(work, actor_id)
            filters = AuditFilters() if filters is None else filters
            validate_filters(filters)
            filters = replace(
                filters,
                start=filters.start.astimezone(UTC) if filters.start else None,
                end=filters.end.astimezone(UTC) if filters.end else None,
            )
            boundary, ceiling, newer, issued = None, None, False, int(self._clock().timestamp())
            if cursor is not None:
                boundary, ceiling, newer, issued = self._decode(cursor, actor_id, filters)
            records = work.audit_history.page(
                filters, before=boundary, ceiling=ceiling, newer=newer
            )
            more = len(records) > PAGE_SIZE
            records = records[:PAGE_SIZE]
            if newer:
                records = tuple(reversed(records))
            if not records:
                if cursor:
                    raise ValidationError(INVALID_CURSOR)
                _, actors = self._present(work, (), filters.actor)
                return AuditPage((), None, None, actors)
            ceiling = ceiling or position(records[0])
            items, actors = self._present(work, records, filters.actor)
            next_cursor = (
                self._encode(position(records[-1]), ceiling, False, issued, actor_id, filters)
                if (more if not newer else True)
                else None
            )
            previous_cursor = (
                self._encode(position(records[0]), ceiling, True, issued, actor_id, filters)
                if (more if newer else boundary is not None)
                else None
            )
            return AuditPage(items, next_cursor, previous_cursor, actors)

    def get_event(self, actor_id, event_id):
        with self.uow() as work:
            self._authorize(work, actor_id)
            if not valid_id(event_id):
                raise NotFoundError("Audit event not found")
            event = work.audit_history.get(event_id)
            if event is None:
                raise NotFoundError("Audit event not found")
            items, _actors = self._present(work, (event,))
            return items[0]

    @staticmethod
    def _authorize(work, actor_id):
        if not valid_id(actor_id):
            raise AuthorizationError("Administrator access required")
        require_admin(work, actor_id)

    @staticmethod
    def _present(work, records, selected_actor=""):
        actor_ids = {event.actor_user_id for event in records if valid_id(event.actor_user_id)}
        if selected_actor:
            actor_ids.add(selected_actor)
        actors = work.audit_history.actor_names(tuple(sorted(actor_ids)))
        names = {}
        for kind, metadata in OBJECTS.items():
            ids = tuple(
                {
                    e.object_id
                    for e in records
                    if e.object_type == kind
                    and valid_id(e.object_id)
                    and not event_deletes_object(e)
                }
            )
            if metadata.resolver is not None and ids:
                try:
                    if metadata.resolver != kind or kind not in AUDIT_NAMED_OBJECT_TYPES:
                        raise ValueError("Invalid resolver mapping")
                    results = work.audit_history.resolve_objects(kind, ids)
                    if set(results) != set(ids) or any(
                        not isinstance(result, AuditObjectRecord) or type(result.exists) is not bool
                        for result in results.values()
                    ):
                        raise ValueError("Incomplete resolver result")
                except Exception:  # noqa: BLE001 - audit reads expose no resolver details.
                    # A fixed technical diagnostic, never identifiers, names, SQL or exception data.
                    logging.getLogger(__name__).error("Audit object resolution failed")
                    raise AuditReadUnavailable("Audit history is temporarily unavailable") from None
                names.update({(kind, key): result for key, result in results.items()})
        return (
            tuple(present_event(event, actors, names) for event in records),
            tuple((key, safe_name(actors.get(key), DELETED_ACTOR)) for key in sorted(actor_ids)),
        )

    def _encode(self, boundary, ceiling, newer, issued, actor, filters):
        payload = json.dumps(
            {
                "v": 1,
                "actor": actor,
                "filters": filter_values(filters),
                "issued": issued,
                "boundary": [boundary.timestamp.isoformat(), boundary.id],
                "ceiling": [ceiling.timestamp.isoformat(), ceiling.id],
                "newer": newer,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self._key, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + signature

    def _decode(self, token, actor, filters):
        try:
            if (
                not isinstance(token, str)
                or len(token) > 2048
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.[a-f0-9]{64}", token)
            ):
                raise ValueError
            encoded, signature = token.split(".")
            expected = hmac.new(self._key, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if set(data) != {"v", "actor", "filters", "issued", "boundary", "ceiling", "newer"}:
                raise ValueError
            if (
                data["v"] != 1
                or data["actor"] != actor
                or data["filters"] != filter_values(filters)
            ):
                raise ValueError
            if type(data["issued"]) is not int or type(data["newer"]) is not bool:
                raise ValueError
            if not 0 <= int(self._clock().timestamp()) - data["issued"] < CURSOR_LIFETIME:
                raise ValueError
            positions = []
            for key in ("boundary", "ceiling"):
                value = data[key]
                if not isinstance(value, list) or len(value) != 2 or not valid_id(value[1]):
                    raise ValueError
                positions.append(AuditPosition(parse_audit_time(value[0]), value[1]))
            boundary, ceiling = positions
            if (boundary.timestamp, boundary.id) > (ceiling.timestamp, ceiling.id):
                raise ValueError
            return boundary, ceiling, data["newer"], data["issued"]
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            raise ValidationError(INVALID_CURSOR) from None
