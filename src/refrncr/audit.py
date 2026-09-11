"""Framework-independent audit contracts. No ORM or interface dependencies.

Historical actions are identified by (object_type, action), not action alone.
Operation registration is explicit; discovery belongs to the CI guard.
"""

import re
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from unicodedata import category, normalize
from uuid import UUID


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
        if self.actor != "required":
            raise AuditContractError("This installation has no anonymous/system audit actor")
        if len(dict(self.context)) != len(self.context) or any(
            kind
            not in {
                "uuid",
                "count",
                "subject",
                "import_format",
                "export_format",
                "dataset_name",
                "version_count",
                "calendar_date",
            }
            for _key, kind in self.context
        ):
            raise AuditContractError("Invalid context contract")


_EVENTS = (
    *(
        EventContract("user", action)
        for action in ("created", "email_changed", "disabled", "enabled", "deleted")
    ),
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
        )
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
    *(EventContract("dataset_record", action) for action in ("created", "updated", "deleted")),
    EventContract(
        "dataset",
        "dataset.records_imported",
        (("format", "import_format"), ("record_count", "count"), ("rejected_row_count", "count")),
    ),
    EventContract("dataset", "dataset.export_requested", (("format", "export_format"),)),
    EventContract("dataset", "dataset.deleted", (("dataset_name", "dataset_name"),)),
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


def validate_event(*, actor_user_id, object_type, object_id, action, details=None):
    """Return a fresh, bounded context; reject unknown fields rather than discard them.

    Never include a rejected value in the exception. Actor authentication is the
    caller service's responsibility; the contract rejects absent identities.
    Bootstrap uses the newly created administrator as its explicitly documented actor.
    """
    contract = EVENTS.get((object_type, action))
    if contract is None:
        raise AuditContractError("Unsupported audit object type/action")
    _identity(actor_user_id)
    _identity(object_id)
    if details is not None and not isinstance(details, dict):
        raise AuditContractError("Audit context must be a mapping")
    supplied = {} if details is None else details
    if set(supplied) != {key for key, _kind in contract.context}:
        raise AuditContractError("Audit context does not match the allowed fields")
    result = {}
    for key, kind in contract.context:
        value = supplied[key]
        if kind == "uuid":
            value = _identity(value)
        elif kind == "count":
            if type(value) is not int or not 0 <= value <= 10_000:
                raise AuditContractError("Audit count is outside the allowed range")
        elif kind == "version_count":
            if type(value) is not int or not 0 <= value <= 2_147_483_647:
                raise AuditContractError("Version count is outside the allowed range")
        elif kind == "calendar_date":
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
            ):
                raise AuditContractError("Invalid effective date")
            try:
                date.fromisoformat(value)
            except ValueError:
                raise AuditContractError("Invalid effective date") from None
        elif kind == "dataset_name":
            # The bounded business-name exception shared by REF-141 and REF-83.
            # Bound before any processing;
            # remove invisible controls, never store description or request context.
            if not isinstance(value, str) or not 1 <= len(value) <= 200:
                raise AuditContractError("Invalid Dataset name snapshot")
            value = "".join(c for c in value if category(c) not in {"Cc", "Cf", "Cs"})
            value = value.strip() or "No name to display"
        else:
            # Bound before normalization, then store only canonical enum members.
            if not isinstance(value, str) or len(value) > 16:
                raise AuditContractError("Invalid structural audit context")
            value = normalize("NFC", value.strip()).casefold()
            allowed = {
                "subject": {"user": "user", "team": "team"},
                "import_format": {"csv": "CSV", "xlsx": "Excel", "excel": "Excel"},
                "export_format": {"csv": "CSV", "xlsx": "Excel", "excel": "Excel", "json": "JSON"},
            }[kind]
            if value not in allowed:
                raise AuditContractError("Invalid structural audit context")
            value = allowed[value]
        result[key] = value
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


CLASSIFICATIONS = frozenset(
    {
        "domain-audited",
        "security logging required",
        "technical logging only",
        "intentionally excluded",
    }
)


@dataclass(frozen=True)
class Operation:
    classification: str
    events: tuple[tuple[str, str], ...] = ()
    rationale: str = ""

    def __post_init__(self):
        if self.classification not in CLASSIFICATIONS:
            raise AuditContractError("Unclassified operation")
        if self.classification == "domain-audited":
            if not self.events or any(event not in EVENTS for event in self.events):
                raise AuditContractError("Audited operation needs supported event contracts")
        elif not self.rationale.strip() or self.events:
            raise AuditContractError("Excluded operation needs a non-empty rationale and no events")


def require_classified(discovered, registered):
    missing = sorted(set(discovered) - set(registered))
    if missing:
        raise AuditContractError("Unclassified operations: " + ", ".join(missing))


# This is deliberately an explicit service registry, including ordinary reads.
# CI discovers all public service methods independently, including new classes.
OPERATIONS = {}


def _register(service, names, classification, *, events=(), rationale="", module="services"):
    for name in names.split():
        symbol = f"refrncr.{module}.{service}.{name}"
        if symbol in OPERATIONS:
            raise AuditContractError("Duplicate operation registration")
        OPERATIONS[symbol] = Operation(classification, events, rationale)


def _audited(service, name, *events, module="services"):
    _register(service, name, "domain-audited", events=events, module=module)


def _read(service, names, rationale="Ordinary authorized read; no business state change."):
    _register(service, names, "intentionally excluded", rationale=rationale)


for _method, _action in (
    ("create_initial_admin", "created"),
    ("create_user", "created"),
    ("change_email", "email_changed"),
    ("disable_user", "disabled"),
    ("enable_user", "enabled"),
    ("delete_user", "deleted"),
):
    _audited("UserService", _method, ("user", _action))
_read("UserService", "list_users")
_read("UserService", "has_users", "Boolean first-setup availability check; no protected content.")
_register(
    "UserService",
    "bootstrap_organisation",
    "technical logging only",
    rationale="Idempotent installation singleton setup before an authenticated actor exists.",
)
for _service, _object, _methods in (
    ("TeamService", "team", (("create_team", "created"), ("rename_team", "renamed"))),
    (
        "MembershipService",
        "membership",
        (("create_membership", "created"), ("delete_membership", "deleted")),
    ),
    (
        "GrantService",
        "grant",
        (
            ("create_grant", "created"),
            ("change_permission", "permission_changed"),
            ("delete_grant", "deleted"),
        ),
    ),
):
    for _method, _action in _methods:
        _audited(_service, _method, (_object, _action))
_audited("TeamService", "delete_team", ("team", "deleted"), ("grant", "deleted"))
_read("TeamService", "list_teams")
_read("MembershipService", "list_team_members list_eligible_users")
_audited("DatasetService", "create_dataset", ("dataset", "created"))
_audited("DatasetService", "delete_dataset", ("dataset", "dataset.deleted"))
_read("DatasetService", "get_dataset_for_deletion")
_audited("DatasetService", "change_owner", ("dataset", "owner_changed"))
_audited(
    "DatasetService",
    "update_dataset",
    ("dataset", "metadata_updated"),
    ("dataset", "owner_changed"),
)
_audited("DatasetService", "grant_export_access", ("dataset_export_access", "granted"))
_audited("DatasetService", "revoke_export_access", ("dataset_export_access", "revoked"))
_read(
    "DatasetService",
    "export_access_context list_datasets list_dataset_overview "
    "get_dataset_context get_dataset get_dataset_details get_dataset_for_edit",
)
_identifier_events = (("dataset", "business_key_defined"), ("dataset", "business_key_updated"))
_audited("DatasetFieldService", "create_field", ("dataset_field", "created"), *_identifier_events)
_audited("DatasetFieldService", "update_field", ("dataset_field", "definition_updated"))
_audited(
    "DatasetFieldService",
    "update_field_and_business_key_membership",
    ("dataset_field", "definition_updated"),
    *_identifier_events,
)
_audited("DatasetFieldService", "delete_field", ("dataset_field", "deleted"))
_audited("DatasetFieldService", "replace_order move_field", ("dataset", "field_order_updated"))
_read("DatasetFieldService", "list_fields get_field")
_audited("DatasetBusinessKeyService", "set_business_key", *_identifier_events)
_read("DatasetBusinessKeyService", "get_business_key")
for _method, _action in (
    ("create_record", "created"),
    ("update_record", "updated"),
    ("delete_record", "deleted"),
):
    _audited("DatasetRecordService", _method, ("dataset_record", _action))
_audited("DatasetRecordService", "export_dataset", ("dataset", "dataset.export_requested"))
_read("DatasetRecordService", "get_record_for_edit list_records get_dataset_for_add")
_audited(
    "DatasetRecordImportService",
    "confirm",
    ("dataset", "dataset.records_imported"),
    module="record_import",
)
_register(
    "DatasetRecordImportService",
    "active state begin navigate select_worksheet "
    "mapping_assistance preview saved_preview cancel revoke_session",
    "intentionally excluded",
    module="record_import",
    rationale="Transient preview/receipt state only; no committed "
    "Records. Rejected or suspicious import attempts belong to REF-31.",
)
_register(
    "AuthenticationService",
    "authenticate",
    "security logging required",
    rationale="Credential verification and optional hash maintenance are authentication "
    "security events (REF-31), not successful business changes.",
)
_register(
    "SessionService",
    "create resolve resolve_with_session revoke",
    "security logging required",
    rationale="Session lifecycle and invalid credentials belong to REF-31; never log tokens.",
)
_register(
    "SessionService",
    "cleanup",
    "technical logging only",
    rationale="Bounded retention cleanup of expired/revoked authentication sessions.",
)
_read(
    "AuthorizationService",
    "effective_permission check_access authorized_resources",
    "Pure policy/read decision; denied or suspicious requests are REF-31 security logging.",
)
_read(
    "DatasetAuthorizationService",
    "active creation_teams can_create_dataset can_view_dataset "
    "maintainable_resources can_maintain_dataset can_export_dataset can_change_dataset_owner "
    "can_manage_export_access",
    "Capability calculation only; the authoritative caller owns "
    "the mutation/extraction event and REF-31 owns denied/suspicious attempts.",
)
# Supporting security/administrative entrypoints are inventoried separately from
# business services. None invents a successful domain event for a denied request.
for _module, _service, _methods, _classification, _reason in (
    (
        "record_import",
        "TransientImportStore",
        "create get update active claim finish consume cancel revoke_session",
        "intentionally excluded",
        "Process-local staged import lifecycle; no durable business change. "
        "Confirmation owns the summary.",
    ),
    (
        "services",
        "ResourceTypeRegistry",
        "register require_existing",
        "technical logging only",
        "Composition-time resource registration/existence checks; no user business operation.",
    ),
    (
        "login_security",
        "SourceIdentityResolver",
        "from_config resolve",
        "security logging required",
        "Trusted source/proxy resolution for REF-31; never trust unvalidated forwarded "
        "request data.",
    ),
    (
        "login_security",
        "LoginAttemptReservation",
        "cancel failed succeeded",
        "security logging required",
        "Authentication attempt outcome belongs to REF-31; process-local reservation "
        "is not domain history.",
    ),
    (
        "login_security",
        "LoginAttemptLimiter",
        "allow reserve entry_count tracked_keys",
        "security logging required",
        "Authentication throttling belongs to REF-31; introspection never exposes "
        "tracked identifiers in domain audit.",
    ),
    (
        "login_security",
        "BrowserAuthenticationAdmissionGuard",
        "try_acquire",
        "security logging required",
        "Authentication capacity rejection belongs to REF-31, with bounded diagnostic "
        "counters only.",
    ),
    (
        "browser_security",
        "BrowserCookiePolicy",
        "for_request",
        "security logging required",
        "Transport/session security decision belongs to REF-31; cookies and session "
        "values are excluded.",
    ),
):
    _register(_service, _methods, _classification, module=_module, rationale=_reason)

for _symbol, _classification, _reason in (
    (
        "refrncr.argon2_benchmark.run_benchmark",
        "technical logging only",
        "Opt-in synthetic password benchmark; no application credentials or business state.",
    ),
    (
        "refrncr.services.DatasetResourceResolver.__call__",
        "intentionally excluded",
        "Internal resource existence read; caller owns authorization and auditing.",
    ),
    (
        "refrncr.cli.prepare_database",
        "technical logging only",
        "Operator/startup Alembic upgrade, including REF-73 ownership backfill; no "
        "authenticated actor. Migration revision and safe outcome are technical diagnostics.",
    ),
    (
        "refrncr.cli.start",
        "technical logging only",
        "Start the installation and run prepare_database; process diagnostics, no business event.",
    ),
    (
        "refrncr.cli.build_parser",
        "intentionally excluded",
        "Help/version and argument parsing disclose no protected application state.",
    ),
    (
        "refrncr.dataset_upgrade.ownership_preflight",
        "technical logging only",
        "Operator-only read/validation of the stopped installation; output is an "
        "upgrade report, not a Dataset export.",
    ),
    (
        "refrncr.dataset_upgrade.read_owner_mapping",
        "technical logging only",
        "Read and validate operator mapping; no persistence or authenticated actor, "
        "never log local paths or content.",
    ),
    (
        "refrncr.dataset_upgrade.require_resolved_ownership",
        "technical logging only",
        "Pre-DDL validation used by Alembic; migration failure is technical, never "
        "successful domain history.",
    ),
    (
        "refrncr.app.health",
        "intentionally excluded",
        "Health response is static and reads no business state.",
    ),
    (
        "refrncr.web.index",
        "intentionally excluded",
        "Authenticated navigation; no data extraction or business mutation.",
    ),
    (
        "refrncr.web.require_csrf",
        "security logging required",
        "Missing, duplicated or mismatched CSRF submissions are REF-31 security "
        "events; never store token values.",
    ),
    (
        "refrncr.web.require_cookie_settings",
        "security logging required",
        "Insecure browser transport is refused; REF-31 logs a safe reason without "
        "cookies or request payload.",
    ),
    (
        "refrncr.web.handle_oversized_request",
        "security logging required",
        "Oversized login/import/form requests belong to REF-31; never store upload or "
        "request contents.",
    ),
    (
        "refrncr.dataset_export.logged_export_stream",
        "technical logging only",
        "Post-preparation transport failure retains the accurate request event; one "
        "fixed diagnostic without exception payload.",
    ),
):
    OPERATIONS[_symbol] = Operation(_classification, rationale=_reason)

_register(
    "AuditHistoryService",
    "list_history get_event",
    "intentionally excluded",
    module="audit_history",
    rationale="Administrator-only audit history read; no business state change or domain event.",
)

_audited(
    "DatasetVersionService",
    "publish",
    ("dataset", "dataset.version_published"),
    module="dataset_versions",
)
_audited(
    "DatasetVersionService",
    "cancel",
    ("dataset", "dataset.version_cancelled"),
    module="dataset_versions",
)
_register(
    "DatasetVersionService",
    "review",
    "intentionally excluded",
    module="dataset_versions",
    rationale="Authorized publication review; no persisted content or event.",
)

for _name in ("overview", "context", "history", "read"):
    _register(
        "DatasetVersionService",
        _name,
        "intentionally excluded",
        module="dataset_versions",
        rationale=(
            "Authorized coherent concept/published read; current REF-73 permissions, "
            "no mutation or domain event."
        ),
    )

for _name in ("publication_preflight", "create_initial_versions"):
    OPERATIONS[f"refrncr.version_upgrade.{_name}"] = Operation(
        "technical logging only",
        rationale=(
            "Stopped-writer operator migration/preflight; no authenticated actor or domain event."
        ),
    )

OPERATIONS = MappingProxyType(OPERATIONS)
