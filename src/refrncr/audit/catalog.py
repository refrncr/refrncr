"""Framework-independent audit contracts. No ORM or interface dependencies.

Historical actions are identified by (object_type, action), not action alone.
Operation registration is explicit; discovery belongs to the CI guard.
"""

import re
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from unicodedata import category, normalize
from uuid import UUID, uuid4

from refrncr.domain import FIELD_KEY_PATTERN


class AuditContractError(ValueError):
    """An event or operation is not covered by the approved audit policy."""


@dataclass(frozen=True)
class EventContract:
    object_type: str
    action: str
    context: tuple[tuple[str, str], ...] = ()
    actor: str = "required"

    def __post_init__(self):
        if any(
            not isinstance(value, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", value) is None
            or len(value) > 100
            for value in (self.object_type, self.action)
        ):
            raise AuditContractError("Audit names must be stable lower-case identifiers")
        if (
            self.actor not in {"required", "unauthenticated", "optional", "absent"}
            or (
                self.actor == "absent"
                and (self.object_type, self.action) != ("user", "password_recovered")
            )
            or (
                self.actor in {"unauthenticated", "optional"}
                and self.object_type not in {"api_key", "security"}
            )
        ):
            raise AuditContractError("This installation has no anonymous/system audit actor")
        if len(dict(self.context)) != len(self.context) or any(
            kind
            not in {
                "uuid",
                "count",
                "subject",
                "import_format",
                "import_mode",
                "export_format",
                "dataset_name",
                "version_count",
                "calendar_date",
                "field_keys",
                "export_policy",
                "period_bound",
                "open_end",
            }
            for _key, kind in self.context
        ):
            raise AuditContractError("Invalid context contract")


_EVENTS = (
    EventContract(
        "installation_settings",
        "export_access_policy_changed",
        (("previous_policy", "export_policy"), ("policy", "export_policy")),
    ),
    *(
        EventContract("api_key", action)
        for action in ("api_key.created", "api_key.revoked", "api_key.authenticated")
    ),
    EventContract("api_key", "api_key.authentication_failed", actor="unauthenticated"),
    EventContract("security", "security.login_failed", actor="unauthenticated"),
    EventContract("security", "security.session_rejected", actor="unauthenticated"),
    EventContract("security", "security.authorization_denied", actor="optional"),
    EventContract("security", "security.csrf_rejected", actor="required"),
    EventContract("security", "security.workflow_token_rejected", actor="required"),
    EventContract(
        "dataset",
        "dataset.concept_discarded",
        (("concept_id", "uuid"),)
        + tuple(
            (name, "version_count")
            for name in (
                "metadata_changes",
                "fields_added",
                "fields_changed",
                "fields_removed",
                "records_added",
                "records_changed",
                "records_removed",
            )
        ),
    ),
    *(
        EventContract("user", action)
        for action in ("created", "email_changed", "disabled", "enabled", "deleted")
    ),
    EventContract("user", "password_recovered", actor="absent"),
    *(EventContract("team", action) for action in ("created", "renamed", "deleted")),
    *(EventContract("membership", action) for action in ("created", "deleted")),
    *(EventContract("grant", action) for action in ("created", "permission_changed", "deleted")),
    EventContract("dataset", "created", (("owner_team_id", "uuid"), ("creator_user_id", "uuid"))),
    EventContract(
        "dataset", "owner_changed", (("previous_owner_team_id", "uuid"), ("owner_team_id", "uuid"))
    ),
    *(
        EventContract("dataset", action)
        for action in (
            "metadata_updated",
            "business_key_defined",
            "business_key_updated",
            "field_order_updated",
            "hierarchy.node_created",
            "hierarchy.node_updated",
            "hierarchy.subtree_deleted",
            "hierarchy.values_updated",
        )
    ),
    EventContract(
        "dataset",
        "hierarchy.import_applied",
        (
            ("additions", "version_count"),
            ("edits", "version_count"),
            ("moves", "version_count"),
            ("removals", "version_count"),
        ),
    ),
    *(
        EventContract(
            "dataset_export_access",
            action,
            (("dataset_id", "uuid"), ("subject_type", "subject"), ("subject_id", "uuid")),
        )
        for action in ("granted", "revoked")
    ),
    *(
        EventContract("dataset_field", action)
        for action in ("created", "definition_updated", "deleted")
    ),
    *(
        EventContract("dataset_record", action)
        for action in ("created", "updated", "deleted", "restored")
    ),
    EventContract(
        "dataset",
        "dataset.records_imported",
        (("format", "import_format"), ("record_count", "count"), ("rejected_row_count", "count")),
    ),
    EventContract(
        "dataset",
        "dataset.mapping_source_updated",
        (("previous_version", "version_count"), ("source_version", "version_count")),
    ),
    EventContract(
        "dataset",
        "dataset.mapping_targets_updated",
        (("format", "import_format"), ("changed", "count"), ("unchanged", "count")),
    ),
    EventContract(
        "dataset",
        "dataset.new_version_imported",
        (("mode", "import_mode"), ("format", "import_format"))
        + tuple(
            (key, "version_count")
            for key in ("added", "changed", "unchanged", "deleted", "total_processed")
        )
        + (("unmapped_fields", "field_keys"),),
    ),
    EventContract("dataset", "dataset.export_requested", (("format", "export_format"),)),
    EventContract(
        "dataset",
        "dataset.history_export_requested",
        (
            ("format", "export_format"),
            ("valid_from", "period_bound"),
            ("valid_to", "period_bound"),
            ("open_end", "open_end"),
        ),
    ),
    EventContract(
        "dataset",
        "dataset.concept_created",
        (("baseline_version_number", "version_count"), ("concept_revision", "version_count")),
    ),
    EventContract("dataset", "dataset.deleted", (("dataset_name", "dataset_name"),)),
    EventContract(
        "dataset",
        "dataset.version_diff_viewed",
        tuple(
            (name, "version_count")
            for name in (
                "baseline_version_number",
                "concept_revision",
                "fields_added",
                "fields_changed",
                "fields_removed",
                "records_added",
                "records_changed",
                "records_removed",
            )
        ),
    ),
    *(
        EventContract(
            "dataset",
            action,
            (
                ("version_number", "version_count"),
                ("effective_date", "calendar_date"),
                ("dataset_name", "dataset_name"),
                ("field_count", "version_count"),
                ("record_count", "version_count"),
            ),
        )
        for action in ("dataset.version_published", "dataset.version_cancelled")
    ),
)
EVENTS = MappingProxyType({(event.object_type, event.action): event for event in _EVENTS})


def _identity(value):
    if not isinstance(value, str) or len(value) != 36:
        raise AuditContractError("Audit identity must be a canonical UUID")
    try:
        if str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise AuditContractError("Audit identity must be a canonical UUID")


def _validate_actor(contract, actor_user_id):
    if contract.actor in {"unauthenticated", "absent"}:
        if actor_user_id is not None:
            message = (
                "Failed authentication has no trusted actor"
                if contract.actor == "unauthenticated"
                else "This operation has no Community user actor"
            )
            raise AuditContractError(message)
    elif contract.actor != "optional" or actor_user_id is not None:
        _identity(actor_user_id)


def _validate_date(value, *, allow_open=False):
    if allow_open and value == "open":
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise AuditContractError("Invalid effective date")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise AuditContractError("Invalid effective date") from None
    return value


def _validate_context_value(kind, value):
    if kind == "uuid":
        return _identity(value)
    if kind in {"count", "version_count"}:
        maximum = 10_000 if kind == "count" else 2_147_483_647
        if type(value) is not int or not 0 <= value <= maximum:
            label = "Audit count" if kind == "count" else "Version count"
            raise AuditContractError(f"{label} is outside the allowed range")
        return value
    if kind in {"calendar_date", "period_bound"}:
        return _validate_date(value, allow_open=kind == "period_bound")
    if kind == "field_keys":
        if not isinstance(value, list) or any(
            not isinstance(item, str) or re.fullmatch(FIELD_KEY_PATTERN, item) is None
            for item in value
        ):
            raise AuditContractError("Invalid field keys")
        if len(set(value)) != len(value):
            raise AuditContractError("Duplicate field keys")
        return list(value)
    if kind == "dataset_name":
        if not isinstance(value, str) or not 1 <= len(value) <= 200:
            raise AuditContractError("Invalid Dataset name snapshot")
        cleaned = "".join(c for c in value if category(c) not in {"Cc", "Cf", "Cs"})
        return cleaned.strip() or "No name to display"
    return _validate_structural_context(kind, value)


def _validate_structural_context(kind, value):
    if not isinstance(value, str) or len(value) > 16:
        raise AuditContractError("Invalid structural audit context")
    value = normalize("NFC", value.strip()).casefold()
    allowed = {
        "export_policy": {value: value for value in ("everyone", "team_grants", "admin_grants")},
        "subject": {"user": "user", "team": "team"},
        "import_mode": {"new_version": "new_version"},
        "import_format": {"csv": "CSV", "xlsx": "Excel", "excel": "Excel"},
        "export_format": {
            "csv": "CSV",
            "csv_zip": "CSV bundle (ZIP)",
            "xlsx": "Excel",
            "excel": "Excel",
            "json": "JSON",
        },
        "open_end": {"empty": "empty", "9999-12-31": "9999-12-31"},
    }[kind]
    if value not in allowed:
        raise AuditContractError("Invalid structural audit context")
    return allowed[value]


def validate_event(*, actor_user_id, object_type, object_id, action, details=None):
    """Return a fresh, bounded context; reject unknown fields rather than discard them.

    Never include a rejected value in the exception. Actor authentication is the
    caller service's responsibility. Actor-free events are allowed only for explicit
    contracts such as local installation recovery. Bootstrap uses the newly created
    administrator as its explicitly documented actor.
    """
    contract = EVENTS.get((object_type, action))
    if contract is None:
        raise AuditContractError("Unsupported audit object type/action")
    _validate_actor(contract, actor_user_id)
    _identity(object_id)
    if details is not None and not isinstance(details, dict):
        raise AuditContractError("Audit context must be a mapping")
    supplied = {} if details is None else details
    if set(supplied) != {key for key, _kind in contract.context}:
        raise AuditContractError("Audit context does not match the allowed fields")
    result = {}
    for key, kind in contract.context:
        result[key] = _validate_context_value(kind, supplied[key])
    return result or None


def record_audit(uow, *, actor_user_id, object_type, object_id, action, details=None):
    context = validate_event(
        actor_user_id=actor_user_id,
        object_type=object_type,
        object_id=object_id,
        action=action,
        details=details,
    )
    uow.add_audit(
        actor_user_id=actor_user_id,
        object_type=object_type,
        object_id=object_id,
        action=action,
        details=context,
    )


def record_security_event(uow_factory, action, *, actor_user_id=None):
    """Persist one content-free security outcome in the existing audit trail."""
    with uow_factory(write=True, serialized=True, translate_errors=True) as work:
        record_audit(
            work,
            actor_user_id=actor_user_id,
            object_type="security",
            object_id=str(uuid4()),
            action=action,
        )
