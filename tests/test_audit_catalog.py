"""CI inventory guard and the production audit context contracts."""

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
            "dataset.version_published dataset.version_cancelled "
            "dataset.version_diff_viewed dataset.concept_created dataset.concept_discarded "
            "dataset.new_version_imported hierarchy.node_created hierarchy.node_updated "
            "hierarchy.subtree_deleted hierarchy.values_updated hierarchy.import_applied"
        ),
        "dataset_field": "created definition_updated deleted",
        "dataset_record": "created updated deleted restored",
        "dataset_export_access": "granted revoked",
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
        }
    )
    assert behaviors == {
        symbol
        for symbol, operation in OPERATIONS.items()
        if operation.classification == "domain-audited"
    }
