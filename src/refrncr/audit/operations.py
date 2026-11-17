"""Explicit audit classifications for application operations."""

from dataclasses import dataclass
from types import MappingProxyType

from refrncr.audit.catalog import EVENTS, AuditContractError

CLASSIFICATIONS = frozenset(
    {
        "domain-audited",
        "security-audited",
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
        if self.classification in {"domain-audited", "security-audited"}:
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

OPERATIONS["refrncr.audit.catalog.record_security_event"] = Operation(
    "security-audited",
    tuple(
        ("security", action)
        for action in (
            "security.login_failed",
            "security.session_rejected",
            "security.authorization_denied",
            "security.csrf_rejected",
            "security.workflow_token_rejected",
        )
    ),
)


def _register(service, names, classification, *, events=(), rationale="", module="services"):
    for name in names.split():
        symbol = f"refrncr.{module}.{service}.{name}"
        if symbol in OPERATIONS:
            raise AuditContractError("Duplicate operation registration")
        OPERATIONS[symbol] = Operation(classification, events, rationale)


def _audited(service, name, *events, module="services"):
    _register(service, name, "domain-audited", events=events, module=module)


def _read(
    service,
    names,
    rationale="Ordinary authorized read; no business state change.",
    *,
    module="services",
):
    _register(service, names, "intentionally excluded", rationale=rationale, module=module)


for _method, _action in (
    ("create_node", "hierarchy.node_created"),
    ("delete_subtree", "hierarchy.subtree_deleted"),
    ("update_node", "hierarchy.node_updated"),
    ("update_node_and_values", "hierarchy.node_updated"),
    ("update_values", "hierarchy.values_updated"),
):
    _audited("HierarchyService", _method, ("dataset", _action), module="hierarchies.service")

_register(
    "HierarchyImportService",
    "confirm",
    "domain-audited",
    events=(("dataset", "hierarchy.import_applied"),),
    module="hierarchies.imports",
)
_register(
    "HierarchyImportService",
    "begin active state preview cancel revoke_session",
    "intentionally excluded",
    module="hierarchies.imports",
    rationale="Transient preview state only; no committed hierarchy change.",
)


for _method, _action in (
    ("create_initial_admin", "created"),
    ("create_user", "created"),
    ("change_email", "email_changed"),
    ("disable_user", "disabled"),
    ("enable_user", "enabled"),
    ("delete_user", "deleted"),
):
    _audited("UserService", _method, ("user", _action), module="identity.users")

_audited(
    "UserService",
    "recover_administrator_password",
    ("user", "password_recovered"),
    module="identity.users",
)
_read("UserService", "list_users", module="identity.users")
_read(
    "UserService",
    "update_theme_preference",
    "Presentation preference persistence; no domain event or protected content change.",
    module="identity.users",
)
_read(
    "UserService",
    "has_users",
    "Boolean first-setup availability check; no protected content.",
    module="identity.users",
)
_register(
    "UserService",
    "bootstrap_organisation",
    "technical logging only",
    rationale="Idempotent installation singleton setup before an authenticated actor exists.",
    module="identity.users",
)
for _service, _object, _methods, _module in (
    (
        "TeamService",
        "team",
        (("create_team", "created"), ("rename_team", "renamed")),
        "identity.teams",
    ),
    (
        "MembershipService",
        "membership",
        (("create_membership", "created"), ("delete_membership", "deleted")),
        "identity.memberships",
    ),
    (
        "GrantService",
        "grant",
        (
            ("create_grant", "created"),
            ("change_permission", "permission_changed"),
            ("delete_grant", "deleted"),
        ),
        "authorization.grants",
    ),
):
    for _method, _action in _methods:
        _audited(_service, _method, (_object, _action), module=_module)
_audited(
    "TeamService",
    "delete_team",
    ("team", "deleted"),
    ("grant", "deleted"),
    module="identity.teams",
)
_read("TeamService", "list_teams", module="identity.teams")
_read(
    "MembershipService",
    "list_team_members list_eligible_users list_relationships",
    module="identity.memberships",
)
_audited(
    "DatasetService",
    "create_dataset",
    ("dataset", "created"),
    ("dataset", "dataset.concept_created"),
    module="datasets.service",
)
_audited(
    "DatasetService",
    "delete_dataset",
    ("dataset", "dataset.deleted"),
    module="datasets.service",
)
_read(
    "DatasetService",
    "get_dataset_for_deletion get_dataset_configuration",
    module="datasets.service",
)
_audited("DatasetService", "change_owner", ("dataset", "owner_changed"), module="datasets.service")
_audited(
    "DatasetService",
    "update_dataset",
    ("dataset", "metadata_updated"),
    ("dataset", "owner_changed"),
    module="datasets.service",
)
_audited(
    "DatasetService",
    "grant_export_access",
    ("dataset_export_access", "granted"),
    module="datasets.service",
)
_audited(
    "DatasetService",
    "revoke_export_access",
    ("dataset_export_access", "revoked"),
    module="datasets.service",
)
_read(
    "DatasetService",
    "export_access_context list_datasets list_dataset_overview "
    "get_dataset_context get_dataset get_dataset_details get_dataset_for_edit "
    "get_visible_dataset_by_id mapping_source mapping_source_choices mapping_usage",
    module="datasets.service",
)
_identifier_events = (("dataset", "business_key_defined"), ("dataset", "business_key_updated"))
_audited(
    "DatasetFieldService",
    "create_field",
    ("dataset_field", "created"),
    *_identifier_events,
    module="datasets.fields",
)
_audited(
    "DatasetFieldService",
    "update_field",
    ("dataset_field", "definition_updated"),
    module="datasets.fields",
)
_audited(
    "DatasetFieldService",
    "update_field_and_business_key_membership",
    ("dataset_field", "definition_updated"),
    *_identifier_events,
    module="datasets.fields",
)
_audited(
    "DatasetFieldService",
    "delete_field",
    ("dataset_field", "deleted"),
    module="datasets.fields",
)
_audited(
    "DatasetFieldService",
    "replace_order move_field",
    ("dataset", "field_order_updated"),
    module="datasets.fields",
)
_read(
    "DatasetFieldService",
    "list_fields get_field list_fields_for_datasets",
    module="datasets.fields",
)
_audited(
    "DatasetBusinessKeyService",
    "set_business_key",
    *_identifier_events,
    module="datasets.business_key",
)
_read("DatasetBusinessKeyService", "get_business_key", module="datasets.business_key")
for _method, _action in (
    ("create_record", "created"),
    ("update_record", "updated"),
    ("delete_record", "deleted"),
    ("restore_record", "restored"),
):
    _audited(
        "DatasetRecordService",
        _method,
        ("dataset_record", _action),
        module="datasets.records",
    )
_audited(
    "DatasetRecordService",
    "export_dataset",
    ("dataset", "dataset.export_requested"),
    module="datasets.records",
)
_read(
    "DatasetRecordService",
    "get_record_for_edit list_records get_dataset_for_add",
    module="datasets.records",
)
_audited(
    "ImportWorkflowMixin",
    "confirm",
    ("dataset", "dataset.records_imported"),
    ("dataset", "dataset.new_version_imported"),
    ("dataset", "dataset.mapping_targets_updated"),
    module="imports.workflows",
)
_register(
    "DatasetRecordImportService",
    "eligibility active state source_format_review begin navigate select_worksheet "
    "mapping_assistance format_assistance submitted_source_formats "
    "submitted_source_format_assistance",
    "intentionally excluded",
    module="imports.service",
    rationale="Transient preview/receipt state only; no committed "
    "Records. Rejected or suspicious import attempts belong to REF-31.",
)
_register(
    "ImportWorkflowMixin",
    "preview saved_preview cancel revoke_session",
    "intentionally excluded",
    module="imports.workflows",
    rationale="Transient preview/receipt state only; no committed "
    "Records. Rejected or suspicious import attempts belong to REF-31.",
)
_register(
    "AuthenticationService",
    "authenticate",
    "security logging required",
    module="identity.authentication",
    rationale="Credential verification and optional hash maintenance are authentication "
    "security events (REF-31), not successful business changes.",
)
_register(
    "SessionService",
    "create resolve resolve_with_session revoke",
    "security logging required",
    module="identity.sessions",
    rationale="Session lifecycle and invalid credentials belong to REF-31; never log tokens.",
)
_register(
    "SessionService",
    "cleanup",
    "technical logging only",
    module="identity.sessions",
    rationale="Bounded retention cleanup of expired/revoked authentication sessions.",
)
_read(
    "AuthorizationService",
    "effective_permission check_access authorized_resources has_capability",
    "Pure policy/read decision; denied or suspicious requests are REF-31 security logging.",
    module="authorization.service",
)
_read(
    "DatasetAuthorizationService",
    "active creation_teams can_create_dataset can_access_community can_view_dataset "
    "maintainable_resources can_maintain_dataset can_export_dataset can_change_dataset_owner "
    "can_manage_export_access can_administer_installation",
    "Capability calculation only; the authoritative caller owns "
    "the mutation/extraction event and REF-31 owns denied/suspicious attempts.",
    module="authorization.datasets",
)
# Supporting security/administrative entrypoints are inventoried separately from
# business services. None invents a successful domain event for a denied request.
for _module, _service, _methods, _classification, _reason in (
    (
        "imports.store",
        "TransientImportStore",
        "create get update active claim finish consume cancel revoke_session revoke_concept",
        "intentionally excluded",
        "Process-local staged import lifecycle; no durable business change. "
        "Confirmation owns the summary.",
    ),
    (
        "authorization.resource_types",
        "ResourceTypeRegistry",
        "register require_existing",
        "technical logging only",
        "Composition-time resource registration/existence checks; no user business operation.",
    ),
    (
        "identity.login_security",
        "SourceIdentityResolver",
        "from_config resolve",
        "security logging required",
        "Trusted source/proxy resolution for REF-31; never trust unvalidated forwarded "
        "request data.",
    ),
    (
        "identity.login_security",
        "LoginAttemptReservation",
        "cancel failed succeeded",
        "security logging required",
        "Authentication attempt outcome belongs to REF-31; process-local reservation "
        "is not domain history.",
    ),
    (
        "identity.login_security",
        "LoginAttemptLimiter",
        "allow reserve entry_count tracked_keys",
        "security logging required",
        "Authentication throttling belongs to REF-31; introspection never exposes "
        "tracked identifiers in domain audit.",
    ),
    (
        "identity.login_security",
        "BrowserAuthenticationAdmissionGuard",
        "try_acquire",
        "security logging required",
        "Authentication capacity rejection belongs to REF-31, with bounded diagnostic "
        "counters only.",
    ),
    (
        "web.security",
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
        "refrncr.identity.argon2_benchmark.run_benchmark",
        "technical logging only",
        "Opt-in synthetic password benchmark; no application credentials or business state.",
    ),
    (
        "refrncr.authorization.dataset_resources.DatasetResourceResolver.__call__",
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
        "refrncr.cli.recover_admin_password",
        "intentionally excluded",
        "CLI adapter only; UserService.recover_administrator_password owns the atomic "
        "credential, session and audit-event transaction.",
    ),
    (
        "refrncr.cli.build_parser",
        "intentionally excluded",
        "Help/version and argument parsing disclose no protected application state.",
    ),
    (
        "refrncr.datasets.dataset_upgrade.ownership_preflight",
        "technical logging only",
        "Operator-only read/validation of the stopped installation; output is an "
        "upgrade report, not a Dataset export.",
    ),
    (
        "refrncr.datasets.dataset_upgrade.read_owner_mapping",
        "technical logging only",
        "Read and validate operator mapping; no persistence or authenticated actor, "
        "never log local paths or content.",
    ),
    (
        "refrncr.datasets.dataset_upgrade.require_resolved_ownership",
        "technical logging only",
        "Pre-DDL validation used by Alembic; migration failure is technical, never "
        "successful domain history.",
    ),
    (
        "refrncr.api.documentation.openapi_document",
        "intentionally excluded",
        "Public API documentation; no business state or credential read and no mutation.",
    ),
    (
        "refrncr.api.documentation.swagger_ui",
        "intentionally excluded",
        "Public API documentation; no business state or credential read and no mutation.",
    ),
    (
        "refrncr.app.health",
        "intentionally excluded",
        "Operational probes read no business state and never create durable audit history.",
    ),
    (
        "refrncr.web.overview.index",
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
        "refrncr.web.errors.handle_oversized_request",
        "security logging required",
        "Oversized login/import/form requests belong to REF-31; never store upload or "
        "request contents.",
    ),
    (
        "refrncr.datasets.export.logged_export_stream",
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
    module="audit.history",
    rationale="Administrator-only audit history read; no business state change or domain event.",
)

_audited(
    "DatasetVersionConceptMixin",
    "update_mapping_source",
    ("dataset", "dataset.mapping_source_updated"),
    module="versions.concepts",
)
_audited(
    "DatasetVersionLifecycleMixin",
    "publish",
    ("dataset", "dataset.version_published"),
    module="versions.lifecycle",
)
_audited(
    "DatasetVersionLifecycleMixin",
    "cancel",
    ("dataset", "dataset.version_cancelled"),
    module="versions.lifecycle",
)
_register(
    "DatasetVersionLifecycleMixin",
    "review",
    "intentionally excluded",
    module="versions.lifecycle",
    rationale="Authorized publication review; no persisted content or event.",
)

for _name in ("overview", "context", "history", "concept_context", "review_discard"):
    _register(
        "DatasetVersionConceptMixin",
        _name,
        "intentionally excluded",
        module="versions.concepts",
        rationale=(
            "Authorized coherent concept/published read; current REF-73 permissions, "
            "no mutation or domain event."
        ),
    )

_register(
    "DatasetVersionLifecycleMixin",
    "read",
    "intentionally excluded",
    module="versions.lifecycle",
    rationale=(
        "Authorized coherent concept/published read; current REF-73 permissions, "
        "no mutation or domain event."
    ),
)

for _name in ("publication_preflight", "create_initial_versions"):
    OPERATIONS[f"refrncr.persistence.version_upgrade.{_name}"] = Operation(
        "technical logging only",
        rationale=(
            "Stopped-writer operator migration/preflight; no authenticated actor or domain event."
        ),
    )

_audited(
    "DatasetDiffService",
    "open_review",
    ("dataset", "dataset.version_diff_viewed"),
    module="datasets.dataset_diff",
)
_register(
    "DatasetDiffService",
    "review field_detail record_detail",
    "intentionally excluded",
    module="datasets.dataset_diff",
    rationale="Child read in one authorized revision-bound review; opening owns the event.",
)

_audited(
    "DatasetVersionConceptMixin",
    "create_concept",
    ("dataset", "dataset.concept_created"),
    module="versions.concepts",
)

_audited(
    "DatasetVersionConceptMixin",
    "discard_concept",
    ("dataset", "dataset.concept_discarded"),
    module="versions.concepts",
)

for _name in ("concept_preflight", "migrate_concepts"):
    OPERATIONS[f"refrncr.persistence.concept_upgrade.{_name}"] = Operation(
        "technical logging only",
        rationale="Stopped-writer concept migration/preflight; no invented actor or domain event.",
    )

_register(
    "ApiKeyService",
    "list_keys get_key",
    "intentionally excluded",
    module="identity.api_keys",
    rationale="Owner-only metadata read; no credential is returned.",
)
_audited(
    "ApiKeyService",
    "create_key",
    ("api_key", "api_key.created"),
    module="identity.api_keys",
)
_audited(
    "ApiKeyService",
    "revoke_key",
    ("api_key", "api_key.revoked"),
    module="identity.api_keys",
)
_register(
    "ApiKeyService",
    "authenticate",
    "security-audited",
    module="identity.api_keys",
    events=(("api_key", "api_key.authentication_failed"),),
    rationale=(
        "REF-169 keeps durable failed-authentication coverage; successful requests use only "
        "safe operational request completion metadata."
    ),
)


_register(
    "DatasetVersionService",
    "read_current",
    "intentionally excluded",
    module="versions.service",
    rationale="Authorized bounded Current read; no per-request durable success audit.",
)
_register(
    "DatasetVersionService",
    "read_versions",
    "intentionally excluded",
    module="versions.service",
    rationale="Authorized bounded published-version reads; no per-request durable success audit.",
)
_register(
    "DatasetVersionService",
    "read_history",
    "intentionally excluded",
    module="versions.service",
    rationale="Authorized derived published-record read; no per-request durable success audit.",
)
_audited(
    "DatasetVersionService",
    "export_history",
    ("dataset", "dataset.history_export_requested"),
    module="versions.service",
)
_register(
    "DatasetAuthorizationService",
    "viewable_resources",
    "intentionally excluded",
    module="authorization.datasets",
    rationale="Bulk current Community Dataset view decision; no mutation or domain event.",
)
_register(
    "DatasetAuthorizationService",
    "exportable_resources",
    "intentionally excluded",
    module="authorization.datasets",
    rationale="Bulk current export authority for published API reads; no mutation or domain event.",
)

_register(
    "OverviewService",
    "read",
    "intentionally excluded",
    module="presentation.overview",
    rationale="Authorized private Overview projection; no business mutation or domain event.",
)
_register(
    "OverviewService",
    "record_visit",
    "intentionally excluded",
    module="presentation.overview",
    rationale="REF-238 private navigation history is explicitly excluded from the audit log.",
)

_register(
    "InstallationSettingsService",
    "get_policy",
    "intentionally excluded",
    module="authorization.installation_settings",
    rationale="Administrator-only installation policy read; no mutation or domain event.",
)
_register(
    "InstallationSettingsService",
    "requires_export_access",
    "intentionally excluded",
    module="authorization.installation_settings",
    rationale="Active-user policy explanation flag; no settings mutation or domain event.",
)
_audited(
    "InstallationSettingsService",
    "change_policy",
    ("installation_settings", "export_access_policy_changed"),
    module="authorization.installation_settings",
)

OPERATIONS = MappingProxyType(OPERATIONS)
