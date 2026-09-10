"""Safe, framework-independent presentation of the authoritative REF-149 catalog."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC
from types import MappingProxyType
from uuid import UUID

from refrncr.audit import EVENTS, AuditContractError, validate_event

# Labels are deliberately explicit. Context and its validation stay in EVENTS.
ACTION_LABELS = MappingProxyType(
    {
        ("user", "created"): "User created",
        ("user", "email_changed"): "User email changed",
        ("user", "disabled"): "User disabled",
        ("user", "enabled"): "User enabled",
        ("user", "deleted"): "User deleted",
        ("team", "created"): "Team created",
        ("team", "renamed"): "Team renamed",
        ("team", "deleted"): "Team deleted",
        ("membership", "created"): "Team membership added",
        ("membership", "deleted"): "Team membership removed",
        ("grant", "created"): "Access granted",
        ("grant", "permission_changed"): "Access permission changed",
        ("grant", "deleted"): "Access revoked",
        ("dataset", "created"): "Dataset created",
        ("dataset", "owner_changed"): "Dataset owning team changed",
        ("dataset", "metadata_updated"): "Dataset metadata updated",
        ("dataset", "business_key_defined"): "Record identifier defined",
        ("dataset", "business_key_updated"): "Record identifier updated",
        ("dataset", "field_order_updated"): "Dataset field order updated",
        ("dataset", "dataset.records_imported"): "Dataset records imported",
        ("dataset", "dataset.export_requested"): "Dataset export requested",
        ("dataset_export_access", "granted"): "Dataset export access granted",
        ("dataset_export_access", "revoked"): "Dataset export access revoked",
        ("dataset_field", "created"): "Dataset field created",
        ("dataset_field", "definition_updated"): "Dataset field definition updated",
        ("dataset_field", "deleted"): "Dataset field deleted",
        ("dataset_record", "created"): "Record created",
        ("dataset_record", "updated"): "Record updated",
        ("dataset_record", "deleted"): "Record deleted",
    }
)


@dataclass(frozen=True, slots=True)
class ObjectPresentation:
    label: str
    resolver: str | None


OBJECTS = MappingProxyType(
    {
        "user": ObjectPresentation("User", "user"),
        "team": ObjectPresentation("Team", "team"),
        "membership": ObjectPresentation("Team membership", None),
        "grant": ObjectPresentation("Access grant", None),
        "dataset": ObjectPresentation("Dataset", "dataset"),
        "dataset_export_access": ObjectPresentation("Dataset export access", None),
        "dataset_field": ObjectPresentation("Dataset field", "dataset_field"),
        "dataset_record": ObjectPresentation("Record", None),
    }
)
CONTEXT_LABELS = MappingProxyType(
    {
        "owner_team_id": "Owning team ID",
        "previous_owner_team_id": "Previous owning team ID",
        "creator_user_id": "Creator user ID",
        "dataset_id": "Dataset ID",
        "subject_type": "Subject type",
        "subject_id": "Subject ID",
        "format": "Format",
        "record_count": "Records imported",
        "rejected_row_count": "Rows rejected",
    }
)
FORMAT_KINDS = frozenset({"uuid", "count", "subject", "import_format", "export_format"})
# REF-149 rejects anonymous/system actors. Bootstrap uses the initial admin's real ID.
SYSTEM_ACTORS = MappingProxyType({})
DELETED_ACTOR = "Deleted user"
UNAVAILABLE_OBJECT = "Deleted or unavailable object"


def require_complete_presentation(
    events=EVENTS, actions=ACTION_LABELS, objects=OBJECTS, context_labels=CONTEXT_LABELS
):
    missing = set(events) - set(actions)
    if missing:
        raise AuditContractError(f"Missing audit action presentation: {sorted(missing)}")
    missing_types = {kind for kind, _action in events} - set(objects)
    if missing_types:
        raise AuditContractError(f"Missing audit object presentation: {sorted(missing_types)}")
    stale = set(actions) - set(events)
    stale_types = set(objects) - {kind for kind, _action in events}
    if stale or stale_types:
        raise AuditContractError(
            f"Stale audit presentation: actions={sorted(stale)}, objects={sorted(stale_types)}"
        )
    for kind, metadata in objects.items():
        if metadata.resolver is not None and metadata.resolver != kind:
            raise AuditContractError(f"Wrong audit object resolver for {kind}: {metadata.resolver}")
    for key, contract in events.items():
        if not actions[key].strip() or not objects[contract.object_type].label.strip():
            raise AuditContractError("Empty audit presentation label")
        missing_context = {
            key for key, _kind in contract.context if not context_labels.get(key, "").strip()
        }
        if missing_context:
            raise AuditContractError(
                f"Missing audit context presentation: {sorted(missing_context)}"
            )
        if contract.actor != "required" or any(
            kind not in FORMAT_KINDS for _, kind in contract.context
        ):
            raise AuditContractError("Missing safe audit actor/context formatter")


def valid_id(value):
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def safe_name(value, fallback):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        return fallback
    # Strip visual direction overrides and controls in presentation only, never in storage.
    value = "".join(c for c in value if unicodedata.category(c) not in {"Cc", "Cf", "Cs"})
    return value.strip() or fallback


def safe_context(event):
    contract = EVENTS.get((event.object_type, event.action))
    if contract is None or not isinstance(event.details, dict):
        return ()
    # Iterate the bounded catalog, not arbitrary historical payloads. Validate a
    # complete candidate with the production validator; unknown keys never escape.
    candidate = {key: event.details.get(key) for key, _kind in contract.context}
    try:
        validated = (
            validate_event(
                actor_user_id=event.actor_user_id,
                object_id=event.object_id,
                object_type=event.object_type,
                action=event.action,
                details=candidate,
            )
            or {}
        )
    except (AuditContractError, TypeError, ValueError):
        return ()
    return tuple(
        (
            CONTEXT_LABELS.get(key, "Additional detail"),
            str(validated[key]).capitalize() if kind == "subject" else str(validated[key]),
        )
        for key, kind in contract.context
    )


@dataclass(frozen=True, slots=True)
class AuditView:
    id: str
    timestamp: str
    actor_id: str
    actor_label: str
    action: str
    action_label: str
    object_type: str
    object_type_label: str
    object_id: str
    object_label: str
    context: tuple[tuple[str, str], ...]


def present_event(event, actor_names, object_names):
    pair = event.object_type, event.action
    known = pair in EVENTS and pair in ACTION_LABELS and event.object_type in OBJECTS
    timestamp = event.timestamp
    timestamp = (
        timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
    )

    # Historical unknown contracts remain traversable, without displaying arbitrary
    # technical strings or unapproved context as though they were supported events.
    def technical(value):
        return (
            value
            if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_.]{0,99}", value)
            else "Unavailable"
        )

    return AuditView(
        event.id,
        timestamp.isoformat(timespec="microseconds"),
        event.actor_user_id if valid_id(event.actor_user_id) else "Unavailable",
        safe_name(actor_names.get(event.actor_user_id), DELETED_ACTOR),
        technical(event.action) if known else "Unavailable",
        ACTION_LABELS[pair] if known else "Unavailable action",
        technical(event.object_type) if known else "Unavailable",
        OBJECTS[event.object_type].label if known else "Unavailable object type",
        event.object_id if valid_id(event.object_id) else "Unavailable",
        safe_name(object_names.get((event.object_type, event.object_id)), UNAVAILABLE_OBJECT),
        safe_context(event) if known else (),
    )
