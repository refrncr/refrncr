"""CI inventory guard and the production audit context contracts."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from refrncr.audit import (
    EVENTS,
    OPERATIONS,
    AuditContractError,
    EventContract,
    Operation,
    record_audit,
    require_classified,
    validate_event,
)
from refrncr.audit_guard import discover_service_operations, verify_inventory

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_inventory_and_coverage_match_current_code():
    operations, routes = verify_inventory(ROOT)
    assert operations == len(OPERATIONS) and routes > 0


def test_guard_detects_new_mutation_extraction_and_read_without_name_heuristics():
    source = """
class FutureService:
    def perform(self): pass
    def export(self): pass
    def view(self): pass
class AnotherWorker:
    def __init__(self, uow): self.uow = uow
    def execute(self): pass
"""
    found = discover_service_operations(source, "refrncr.future")
    assert found == {
        f"refrncr.future.{name}"
        for name in (
            "FutureService.perform",
            "FutureService.export",
            "FutureService.view",
            "AnotherWorker.execute",
        )
    }
    with pytest.raises(AuditContractError, match="refrncr.future.FutureService.perform"):
        require_classified(found, OPERATIONS)
    # A read is classified as a read/exclusion, never inferred to be a mutation.
    exclusion = Operation("intentionally excluded", rationale="Ordinary read")
    require_classified(found, dict.fromkeys(found, exclusion))


@pytest.mark.parametrize(
    "classification",
    ["intentionally excluded", "technical logging only", "security logging required"],
)
@pytest.mark.parametrize("reason", ["", "   "])
def test_exclusion_requires_nonempty_reason(classification, reason):
    with pytest.raises(AuditContractError, match="rationale"):
        Operation(classification, rationale=reason)


def test_correctly_audited_registration_and_invalid_classification():
    operation = Operation("domain-audited", (("dataset", "dataset.export_requested"),))
    require_classified({"Example.export"}, {"Example.export": operation})
    with pytest.raises(AuditContractError, match="Unclassified"):
        Operation("perhaps")
    with pytest.raises(AuditContractError, match="supported"):
        Operation("domain-audited", (("dataset", "unknown"),))
    with pytest.raises(AuditContractError):
        Operation("domain-audited")


def test_historical_action_names_and_object_types_are_stable():
    expected = {
        "api_key": (
            "api_key.created api_key.revoked api_key.authenticated api_key.authentication_failed"
        ),
        "security": (
            "security.login_failed security.session_rejected security.authorization_denied "
            "security.csrf_rejected security.workflow_token_rejected"
        ),
        "user": "created email_changed disabled enabled deleted",
        "team": "created renamed deleted",
        "membership": "created deleted",
        "grant": "created permission_changed deleted",
        "dataset": (
            "created owner_changed metadata_updated business_key_defined "
            "business_key_updated field_order_updated dataset.records_imported "
            "dataset.export_requested dataset.deleted "
            "dataset.history_export_requested "
            "dataset.version_published dataset.version_cancelled "
            "dataset.version_diff_viewed dataset.concept_created dataset.concept_discarded "
            "dataset.new_version_imported dataset.mapping_targets_updated "
            "dataset.mapping_source_updated "
            "hierarchy.node_created hierarchy.node_updated "
            "hierarchy.subtree_deleted hierarchy.values_updated hierarchy.import_applied"
        ),
        "dataset_field": "created definition_updated deleted",
        "dataset_record": "created updated deleted restored",
        "dataset_export_access": "granted revoked",
        "installation_settings": "export_access_policy_changed",
    }
    assert set(EVENTS) == {
        (kind, action) for kind, actions in expected.items() for action in actions.split()
    }
    assert {event.actor for event in EVENTS.values()} == {
        "required",
        "unauthenticated",
        "optional",
    }
    produced = {event for operation in OPERATIONS.values() for event in operation.events}
    assert set(EVENTS) - produced == {("api_key", "api_key.authenticated")}


@pytest.mark.parametrize(
    "kind,action,actor",
    [
        ("Dataset", "created", "required"),
        ("dataset", "Created", "required"),
        ("dataset", "created", "system"),
        ("dataset", "x" * 101, "required"),
    ],
)
def test_invalid_event_contract_names_or_actor_are_rejected(kind, action, actor):
    with pytest.raises(AuditContractError):
        EventContract(kind, action, actor=actor)


def event(**changes):
    return (
        dict(
            actor_user_id=str(uuid4()),
            object_type="dataset",
            object_id=str(uuid4()),
            action="dataset.export_requested",
            details={"format": "csv"},
        )
        | changes
    )


@pytest.mark.parametrize(
    "identity", [None, "", "anonymous", "x" * 1000, "ABCDEF00-0000-0000-0000-000000000000"]
)
def test_actor_and_object_require_stable_identity(identity):
    for key in ("actor_user_id", "object_id"):
        with pytest.raises(AuditContractError):
            validate_event(**event(**{key: identity}))


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "api_key",
        "credentials",
        "session",
        "csrf_token",
        "filename",
        "path",
        "sql",
        "stack_trace",
        "description",
        "old_values",
        "new_values",
        "upload",
        "export",
    ],
)
def test_secret_and_business_context_is_rejected_before_adapter(key):
    calls = []
    uow = SimpleNamespace(add_audit=lambda **values: calls.append(values))
    with pytest.raises(AuditContractError) as error:
        record_audit(uow, **event(details={"format": "csv", key: "sensitive-sentinel"}))
    assert calls == []
    assert "sensitive-sentinel" not in str(error.value)


@pytest.mark.parametrize(
    "value", ["x" * 10000, "csv\nprivate", "/tmp/file.csv", {"format": "csv"}, ["csv"], 1]
)
def test_unbounded_or_nonstructural_context_rejected(value):
    with pytest.raises(AuditContractError):
        validate_event(**event(details={"format": value}))


def test_context_normalization_allowlist_missing_fields_and_count_limits():
    assert validate_event(**event(details={"format": "  XlSX  "})) == {"format": "Excel"}
    context = {"format": " CSV ", "record_count": 10000, "rejected_row_count": 0}
    assert validate_event(**event(action="dataset.records_imported", details=context)) == {
        "format": "CSV",
        "record_count": 10000,
        "rejected_row_count": 0,
    }
    assert context["format"] == " CSV "  # no mutation or shared caller-owned context
    for invalid in (-1, 10001, True, "2", 1.5):
        with pytest.raises(AuditContractError):
            validate_event(
                **event(
                    action="dataset.records_imported", details=context | {"record_count": invalid}
                )
            )
    for details in (None, {}, {"format": "json"}, {"format": "csv", "record_count": 1}):
        with pytest.raises(AuditContractError):
            validate_event(**event(action="dataset.records_imported", details=details))


def test_catalog_and_guard_have_no_framework_or_persistence_dependencies():
    for filename in ("audit.py", "audit_guard.py"):
        tree = ast.parse((ROOT / "src/refrncr" / filename).read_text())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imports & {
            "flask",
            "fastapi",
            "sqlalchemy",
            "refrncr.models",
            "refrncr.persistence",
            "refrncr.web",
        }


def test_application_template_boundary_has_no_inline_markup_or_unsafe_shared_panel():
    python_sources = tuple((ROOT / "src/refrncr").rglob("*.py"))
    source = "\n".join(path.read_text() for path in python_sources)
    for forbidden in (
        "render_template_string",
        "flask_render_template_string",
        '"<form',
        '"<dialog',
        '"{% ',
        '"{{ ',
    ):
        assert forbidden not in source

    side_panel = (ROOT / "src/refrncr/templates/components/side_panel.html").read_text()
    assert "|safe" not in side_panel
    assert "record" not in side_panel
    assert "permission" not in side_panel
    assert "csrf" not in side_panel


def test_every_audited_operation_has_behavior_cases():
    from support import MUTATIONS

    behaviors = {
        f"refrncr.services.{name}"
        for name in MUTATIONS
        if not name.startswith(("DatasetRecordImportService", "DatasetVersionService"))
    }
    behaviors.update(
        {
            "refrncr.record_import.DatasetRecordImportService.confirm",
            "refrncr.dataset_versions.DatasetVersionService.publish",
            "refrncr.dataset_versions.DatasetVersionService.cancel",
            "refrncr.dataset_versions.DatasetVersionService.update_mapping_source",
            "refrncr.dataset_versions.DatasetVersionService.export_history",
            "refrncr.api_keys.ApiKeyService.create_key",
            "refrncr.api_keys.ApiKeyService.revoke_key",
            "refrncr.services.UserService.create_initial_admin",
            "refrncr.services.DatasetRecordService.export_dataset",
            "refrncr.dataset_diff.DatasetDiffService.open_review",
            "refrncr.dataset_versions.DatasetVersionService.create_concept",
            "refrncr.dataset_versions.DatasetVersionService.discard_concept",
            "refrncr.hierarchy.HierarchyService.create_node",
            "refrncr.hierarchy.HierarchyService.delete_subtree",
            "refrncr.hierarchy.HierarchyService.update_node",
            "refrncr.hierarchy.HierarchyService.update_node_and_values",
            "refrncr.hierarchy.HierarchyService.update_values",
            "refrncr.hierarchy_import.HierarchyImportService.confirm",
            "refrncr.installation_settings.InstallationSettingsService.change_policy",
        }
    )
    assert behaviors == {
        symbol
        for symbol, operation in OPERATIONS.items()
        if operation.classification == "domain-audited"
    }


def test_cli_inventory_matches_real_parser():
    from refrncr.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["start"]).command == "start"
    assert parser.parse_args(["dataset-ownership-preflight", "--upgrade"]).upgrade
    assert (
        parser.parse_args(
            ["user", "create-admin", "--email", "test@example.com", "--display-name", "Test"]
        ).user_command
        == "create-admin"
    )
    inventory = json.loads((ROOT / "docs/development/audit-inventory.json").read_text())
    assert all(row["operations"] for row in inventory["cli_commands"])


def test_ci_guard_fails_on_new_service_in_real_repository(monkeypatch):
    original = Path.read_text

    def changed(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path == ROOT / "src/refrncr/services.py":
            source += "\nclass FutureService:\n    def perform(self): pass\n"
        return source

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(AuditContractError, match="refrncr.services.FutureService.perform"):
        verify_inventory(ROOT)


def test_ci_guard_rejects_audit_catalog_bypass_in_blueprint_module(monkeypatch):
    original = Path.read_text

    def changed(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path == ROOT / "src/refrncr/web/auth.py":
            source += "\ndef bypass(uow):\n    uow.add_audit(action='bypass')\n"
        return source

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(AuditContractError, match="Audit catalog bypass in refrncr.web.auth"):
        verify_inventory(ROOT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("domain_audit", False),
        ("security_logging", "yes"),
        ("object_identifier", ""),
        ("security_handoff", ""),
        ("technical_logging", ""),
        ("kind", "unknown"),
        ("interface_entrypoints", ["Programmatic caller"]),
    ],
)
@pytest.mark.slow
def test_guard_rejects_inconsistent_or_missing_matrix_decisions(monkeypatch, field, value):
    original = Path.read_text

    def changed(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path == ROOT / "docs/development/audit-coverage.json":
            matrix = json.loads(source)
            row = next(
                item
                for item in matrix["operations"]
                if item["service"] == "refrncr.services.UserService.create_initial_admin"
            )
            row[field] = value
            return json.dumps(matrix)
        return source

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(AuditContractError, match="UserService.create_initial_admin"):
        verify_inventory(ROOT)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_guard_requires_exact_inventory_registration(monkeypatch, change):
    original = Path.read_text

    def changed(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path == ROOT / "docs/development/audit-inventory.json":
            inventory = json.loads(source)
            names = inventory["administrative_operations"]
            if change == "missing":
                names.pop()
            else:
                names.append(names[0] if change == "duplicate" else "refrncr.future.perform")
            return json.dumps(inventory)
        return source

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(AuditContractError, match="Operation inventory mismatch"):
        verify_inventory(ROOT)


def test_http_inventory_traces_current_service_calls_and_shared_helpers():
    """Review the current composition independently of the catalog registrations.

    Explicit receiver aliases reflect Flask composition, not guesses about which
    method names mutate. Dynamic user lifecycle dispatch is covered by behavior tests.
    """
    receivers = {
        "api_keys": "ApiKeyService",
        "api_key_service": "ApiKeyService",
        "users": "UserService",
        "authentication": "AuthenticationService",
        "sessions": "SessionService",
        "teams": "TeamService",
        "memberships": "MembershipService",
        "authorization": "AuthorizationService",
        "authorization_service": "AuthorizationService",
        "datasets": "DatasetService",
        "dataset_service": "DatasetService",
        "service": "DatasetService",
        "dataset_fields": "DatasetFieldService",
        "field_service": "DatasetFieldService",
        "dataset_business_keys": "DatasetBusinessKeyService",
        "business_key_service": "DatasetBusinessKeyService",
        "dataset_records": "DatasetRecordService",
        "record_service": "DatasetRecordService",
        "record_imports": "DatasetRecordImportService",
        "audit_history": "AuditHistoryService",
        "dataset_versions": "DatasetVersionService",
        "dataset_diff": "DatasetDiffService",
    }
    web_package = ROOT / "src/refrncr/web"
    functions = {
        node.name: node
        for path in sorted(web_package.glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.FunctionDef)
    }

    def service_calls(name, seen):
        if name in seen:
            return set()
        seen.add(name)
        found = set()
        for node in ast.walk(functions[name]):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Attribute):
                receiver = function.value
                if isinstance(receiver, ast.Attribute) and receiver.attr == "datasets":
                    found.add(f"refrncr.services.DatasetAuthorizationService.{function.attr}")
                elif (
                    isinstance(receiver, ast.Name)
                    and receiver.id in receivers
                    and function.attr != "uow"
                    and not function.attr.startswith("_")
                ):
                    module = {
                        "api_keys": "api_keys",
                        "api_key_service": "api_keys",
                        "record_imports": "record_import",
                        "audit_history": "audit_history",
                        "dataset_versions": "dataset_versions",
                        "dataset_diff": "dataset_diff",
                    }.get(receiver.id, "services")
                    found.add(f"refrncr.{module}.{receivers[receiver.id]}.{function.attr}")
            elif isinstance(function, ast.Name) and function.id in functions:
                found.update(service_calls(function.id, seen))
        return found

    inventory = json.loads((ROOT / "docs/development/audit-inventory.json").read_text())
    for route in inventory["routes"]:
        name = route["symbol"].split(".")[-1]
        if name in functions:
            missing = service_calls(name, set()) - set(route["operations"])
            assert not missing, f"Missing service trace from {route['symbol']}: {sorted(missing)}"


def test_guard_checks_nested_documentation_operation(monkeypatch):
    original = Path.read_text

    def missing(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path == ROOT / "src/refrncr/api/documentation.py":
            return source.replace("def openapi_document():", "def removed_document():").replace(
                "@app.get(", "@other.get("
            )
        return source

    monkeypatch.setattr(Path, "read_text", missing)
    with pytest.raises(AuditContractError):
        verify_inventory(ROOT)


@pytest.mark.parametrize(
    "keys", [None, "label", ["label", "label"], ["x" * 101], ["<script>"], [None]]
)
def test_import_audit_rejects_invalid_omitted_field_keys(keys):
    details = dict(
        mode="new_version",
        format="CSV",
        added=0,
        changed=0,
        unchanged=1,
        deleted=0,
        total_processed=1,
        unmapped_fields=keys,
    )
    with pytest.raises(AuditContractError):
        validate_event(**event(action="dataset.new_version_imported", details=details))


def test_import_audit_keeps_schema_key_order_and_copies_the_list():
    keys = ["z_note", "a_label"]
    details = dict(
        mode="new_version",
        format="CSV",
        added=0,
        changed=0,
        unchanged=1,
        deleted=0,
        total_processed=1,
        unmapped_fields=keys,
    )
    result = validate_event(**event(action="dataset.new_version_imported", details=details))
    assert result["unmapped_fields"] == keys
    keys.clear()
    assert result["unmapped_fields"] == ["z_note", "a_label"]
