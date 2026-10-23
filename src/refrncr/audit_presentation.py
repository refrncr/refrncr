"""Safe, framework-independent presentation of the authoritative REF-149 catalog."""

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from refrncr.audit import EVENTS, AuditContractError, validate_event
from refrncr.ports import AUDIT_NAMED_OBJECT_TYPES


@dataclass(frozen=True, slots=True)
class ActionPresentation:
    label: str
    deletes_object: bool


# Every catalog pair needs an explicit presentation decision, including non-deletes.
# Event production, safe context and technical contracts remain solely in EVENTS.
ACTION_PRESENTATIONS = MappingProxyType(
    {
        ("api_key", "api_key.created"): ActionPresentation("API key created", False),
        ("api_key", "api_key.revoked"): ActionPresentation("API key revoked", False),
        ("api_key", "api_key.authenticated"): ActionPresentation(
            "API key authentication succeeded", False
        ),
        ("api_key", "api_key.authentication_failed"): ActionPresentation(
            "API key authentication failed", False
        ),
        ("security", "security.login_failed"): ActionPresentation("Login failed", False),
        ("security", "security.session_rejected"): ActionPresentation("Session rejected", False),
        ("security", "security.authorization_denied"): ActionPresentation(
            "Authorization denied", False
        ),
        ("security", "security.csrf_rejected"): ActionPresentation(
            "Protected form token rejected", False
        ),
        ("security", "security.workflow_token_rejected"): ActionPresentation(
            "Protected workflow token rejected", False
        ),
        ("user", "created"): ActionPresentation("User created", False),
        ("user", "email_changed"): ActionPresentation("User email changed", False),
        ("user", "disabled"): ActionPresentation("User disabled", False),
        ("user", "enabled"): ActionPresentation("User enabled", False),
        ("user", "deleted"): ActionPresentation("User deleted", True),
        ("team", "created"): ActionPresentation("Team created", False),
        ("team", "renamed"): ActionPresentation("Team renamed", False),
        ("team", "deleted"): ActionPresentation("Team deleted", True),
        ("membership", "created"): ActionPresentation("Team membership added", False),
        ("membership", "deleted"): ActionPresentation("Team membership removed", True),
        ("grant", "created"): ActionPresentation("Access granted", False),
        ("grant", "permission_changed"): ActionPresentation("Access permission changed", False),
        ("grant", "deleted"): ActionPresentation("Access revoked", True),
        ("dataset", "dataset.concept_created"): ActionPresentation(
            "Dataset concept created", False
        ),
        ("dataset", "dataset.concept_discarded"): ActionPresentation(
            "Dataset concept discarded", False
        ),
        ("dataset", "created"): ActionPresentation("Dataset created", False),
        ("dataset", "dataset.version_diff_viewed"): ActionPresentation(
            "Dataset changes reviewed", False
        ),
        ("dataset", "dataset.version_published"): ActionPresentation(
            "Dataset version published", False
        ),
        ("dataset", "dataset.version_cancelled"): ActionPresentation(
            "Dataset version cancelled", False
        ),
        ("dataset", "dataset.deleted"): ActionPresentation("Dataset deleted", True),
        ("dataset", "owner_changed"): ActionPresentation("Dataset owning team changed", False),
        ("dataset", "metadata_updated"): ActionPresentation("Dataset metadata updated", False),
        ("dataset", "hierarchy.node_created"): ActionPresentation("Hierarchy node created", False),
        ("dataset", "hierarchy.node_updated"): ActionPresentation("Hierarchy node updated", False),
        ("dataset", "hierarchy.subtree_deleted"): ActionPresentation(
            "Hierarchy subtree deleted", False
        ),
        ("dataset", "hierarchy.values_updated"): ActionPresentation(
            "Hierarchy node values updated", False
        ),
        ("dataset", "hierarchy.import_applied"): ActionPresentation("Hierarchy imported", False),
        ("dataset", "business_key_defined"): ActionPresentation("Record identifier defined", False),
        ("dataset", "business_key_updated"): ActionPresentation("Record identifier updated", False),
        ("dataset", "field_order_updated"): ActionPresentation(
            "Dataset field order updated", False
        ),
        ("dataset", "dataset.records_imported"): ActionPresentation(
            "Dataset records imported", False
        ),
        ("dataset", "dataset.export_requested"): ActionPresentation(
            "Dataset export requested", False
        ),
        ("dataset_export_access", "granted"): ActionPresentation(
            "Dataset export access granted", False
        ),
        ("dataset_export_access", "revoked"): ActionPresentation(
            "Dataset export access revoked", True
        ),
        ("dataset_field", "created"): ActionPresentation("Dataset field created", False),
        ("dataset_field", "definition_updated"): ActionPresentation(
            "Dataset field definition updated", False
        ),
        ("dataset_field", "deleted"): ActionPresentation("Dataset field deleted", True),
        ("dataset_record", "created"): ActionPresentation("Record created", False),
        ("dataset_record", "restored"): ActionPresentation("Record restored", False),
        ("dataset", "dataset.mapping_source_updated"): ActionPresentation(
            "Mapping source updated", False
        ),
        ("dataset", "dataset.mapping_targets_updated"): ActionPresentation(
            "Mapping targets updated", False
        ),
        ("dataset", "dataset.new_version_imported"): ActionPresentation(
            "New Record version imported", False
        ),
        ("dataset_record", "updated"): ActionPresentation("Record updated", False),
        ("dataset_record", "deleted"): ActionPresentation("Record deleted", True),
    }
)
ACTION_LABELS = MappingProxyType({pair: item.label for pair, item in ACTION_PRESENTATIONS.items()})


@dataclass(frozen=True, slots=True)
class ObjectPresentation:
    label: str
    resolver: str | None


OBJECTS = MappingProxyType(
    {
        "api_key": ObjectPresentation("API key", None),
        "security": ObjectPresentation("Security attempt", None),
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
        "version_number": "Version number",
        "baseline_version_number": "Baseline version",
        "previous_version": "Previous source version",
        "source_version": "Source version",
        "concept_revision": "Draft revision",
        "concept_id": "Concept ID",
        "metadata_changes": "Dataset properties changed",
        "fields_added": "Fields added",
        "fields_changed": "Fields changed",
        "fields_removed": "Fields removed",
        "records_added": "Records added",
        "records_changed": "Records changed",
        "records_removed": "Records removed",
        "effective_date": "Effective date",
        "field_count": "Fields",
        "dataset_id": "Dataset ID",
        "dataset_name": "Dataset name snapshot",
        "subject_type": "Subject type",
        "subject_id": "Subject ID",
        "format": "Format",
        "record_count": "Records",
        "mode": "Import mode",
        "added": "Added",
        "additions": "Hierarchy nodes added",
        "changed": "Changed",
        "edits": "Hierarchy nodes edited",
        "unchanged": "Unchanged",
        "moves": "Hierarchy nodes moved",
        "deleted": "Deleted",
        "removals": "Hierarchy nodes removed",
        "total_processed": "Total processed",
        "rejected_row_count": "Rows rejected",
    }
)
FORMAT_KINDS = frozenset(
    {
        "uuid",
        "count",
        "subject",
        "import_format",
        "import_mode",
        "export_format",
        "dataset_name",
        "version_count",
        "calendar_date",
    }
)
# REF-149 rejects anonymous/system actors. Bootstrap uses the initial admin's real ID.
SYSTEM_ACTORS = MappingProxyType({})
DELETED_ACTOR = "Deleted user"
UNNAMED_OBJECT = "No name to display"
DELETED_OBJECT = "Deleted object"


def require_complete_presentation(
    events=EVENTS,
    actions=ACTION_LABELS,
    objects=OBJECTS,
    context_labels=CONTEXT_LABELS,
    *,
    presentations=ACTION_PRESENTATIONS,
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
    missing_decisions = set(events) - set(presentations)
    stale_decisions = set(presentations) - set(events)
    if missing_decisions or stale_decisions:
        raise AuditContractError(
            f"Invalid audit action decisions: missing={sorted(missing_decisions)}, "
            f"stale={sorted(stale_decisions)}"
        )
    for pair, metadata in presentations.items():
        if (
            not isinstance(metadata, ActionPresentation)
            or type(metadata.deletes_object) is not bool
            or metadata.label != actions[pair]
        ):
            raise AuditContractError(f"Explicit audit delete presentation required: {pair}")
    for kind, metadata in objects.items():
        if metadata.resolver is not None and (
            metadata.resolver != kind or metadata.resolver not in AUDIT_NAMED_OBJECT_TYPES
        ):
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
        if contract.actor not in {"required", "unauthenticated", "optional"} or any(
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
            "Dataset name at deletion"
            if key == "dataset_name" and event.action == "dataset.deleted"
            else "Records imported"
            if key == "record_count" and event.action == "dataset.records_imported"
            else CONTEXT_LABELS.get(key, "Additional detail"),
            str(validated[key]).capitalize() if kind == "subject" else str(validated[key]),
        )
        for key, kind in contract.context
    )


@dataclass(frozen=True, slots=True)
class ObjectResolution:
    status: Literal["resolved", "unnamed", "deleted"]
    label: str


def event_deletes_object(event):
    pair = event.object_type, event.action
    metadata = ACTION_PRESENTATIONS.get(pair)
    return pair in EVENTS and metadata is not None and metadata.deletes_object is True


def present_object(result=None, *, deleted_by_event=False):
    # No resolver/result is not evidence of deletion. Existence is explicit;
    # an empty, withheld or unsafe name on an existing object is merely unnamed.
    if deleted_by_event:
        return ObjectResolution("deleted", DELETED_OBJECT)
    if result is None:
        return ObjectResolution("unnamed", UNNAMED_OBJECT)
    if not result.exists:
        return ObjectResolution("deleted", DELETED_OBJECT)
    name = safe_name(result.name, "")
    return (
        ObjectResolution("resolved", name) if name else ObjectResolution("unnamed", UNNAMED_OBJECT)
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
    object_status: str
    object_label: str
    context: tuple[tuple[str, str], ...]


def present_event(event, actor_names, objects):
    pair = event.object_type, event.action
    known = pair in EVENTS and pair in ACTION_LABELS and event.object_type in OBJECTS
    obj = present_object(
        objects.get((event.object_type, event.object_id)) if known else None,
        deleted_by_event=known and event_deletes_object(event),
    )
    if pair == ("dataset", "dataset.deleted"):
        context = safe_context(event)
        if context:
            obj = ObjectResolution("deleted", f"{context[0][1]} (deleted)")
    if pair in {("dataset", "dataset.version_published"), ("dataset", "dataset.version_cancelled")}:
        context = dict(safe_context(event))
        name = context.get("Dataset name snapshot")
        if name and obj.status != "resolved":
            obj = ObjectResolution(
                obj.status, name + (" (deleted)" if obj.status == "deleted" else "")
            )
    timestamp = event.timestamp

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
        "Unauthenticated"
        if event.actor_user_id is None and known
        else safe_name(actor_names.get(event.actor_user_id), DELETED_ACTOR),
        technical(event.action) if known else "Unavailable",
        ACTION_LABELS[pair] if known else "Unavailable action",
        technical(event.object_type) if known else "Unavailable",
        OBJECTS[event.object_type].label if known else "Unavailable object type",
        event.object_id if valid_id(event.object_id) else "Unavailable",
        obj.status,
        obj.label,
        safe_context(event) if known else (),
    )
