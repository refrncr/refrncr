"""Static inventory verification for CI; contains no framework/persistence imports."""

import ast
import json
from pathlib import Path

from refrncr.audit import EVENTS, OPERATIONS, AuditContractError, require_classified

SUPPORT_CLASSES = frozenset(
    {
        "ResourceTypeRegistry",
        "TransientImportStore",
        "SourceIdentityResolver",
        "LoginAttemptReservation",
        "LoginAttemptLimiter",
        "BrowserAuthenticationAdmissionGuard",
        "BrowserCookiePolicy",
    }
)
ROUTE_DECORATORS = {
    "app.get",
    "app.post",
    "app.route",
    "blueprint.get",
    "blueprint.post",
    "blueprint.route",
    "dataset_route",
    "import_route",
}


def discover_service_operations(source, module):
    found = set()
    for cls in ast.parse(source).body:
        if not isinstance(cls, ast.ClassDef):
            continue
        injected_uow = any(
            isinstance(n, ast.Attribute)
            and isinstance(n.ctx, ast.Store)
            and ast.unparse(n) == "self.uow"
            for n in ast.walk(cls)
        )
        if not (cls.name.endswith("Service") or injected_uow or cls.name in SUPPORT_CLASSES):
            continue
        for method in cls.body:
            if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef) and (
                not method.name.startswith("_") or method.name == "__call__"
            ):
                found.add(f"{module}.{cls.name}.{method.name}")
    return found


def discover_routes(source, module):
    routes = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            name = ast.unparse(decorator.func)
            if name not in ROUTE_DECORATORS:
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                raise AuditContractError("Route needs explicit inventory: " + node.name)
            methods = next(
                (ast.literal_eval(k.value) for k in decorator.keywords if k.arg == "methods"),
                ["POST"] if name.endswith(".post") else ["GET"],
            )
            routes.append(
                {
                    "symbol": f"{module}.{node.name}",
                    "path": decorator.args[0].value,
                    "methods": sorted(methods),
                }
            )
    return routes


def verify_inventory(root):
    root = Path(root)
    inventory = json.loads((root / "docs/development/audit-inventory.json").read_text())
    matrix = json.loads((root / "docs/development/audit-coverage.json").read_text())
    discovered, routes, module_commands = set(), [], []
    for path in (root / "src/refrncr").rglob("*.py"):
        if "migrations" in path.parts:
            continue  # Operator prepare_database owns the complete Alembic revision chain.
        module_parts = path.relative_to(root / "src").with_suffix("").parts
        if module_parts[-1] == "__init__":
            module_parts = module_parts[:-1]
        module = ".".join(module_parts)
        source = path.read_text()
        tree = ast.parse(source)
        if any(
            isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
            for node in tree.body
        ):
            module_commands.append(module)
        discovered.update(discover_service_operations(source, module))
        routes.extend(discover_routes(source, module))
        if module in {
            "refrncr.services",
            "refrncr.record_import",
            "refrncr.web",
            "refrncr.dataset_versions",
            "refrncr.dataset_diff",
            "refrncr.api_keys",
            "refrncr.hierarchy",
        }:
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and (
                    ast.unparse(node.func) in {"AuditEvent", "uow.add_audit"}
                ):
                    raise AuditContractError("Audit catalog bypass in " + module)
    require_classified(discovered, OPERATIONS)
    if sorted(module_commands) != sorted(inventory["module_commands"]):
        raise AuditContractError("Module CLI inventory mismatch: " + ", ".join(module_commands))
    documented_list = (
        inventory["service_operations"]
        + inventory["support_operations"]
        + inventory["administrative_operations"]
    )
    documented = set(documented_list)
    if len(documented_list) != len(documented) or documented != set(OPERATIONS):
        difference = documented ^ set(OPERATIONS)
        raise AuditContractError("Operation inventory mismatch: " + ", ".join(sorted(difference)))
    if discovered - documented:
        raise AuditContractError("Missing inventory: " + ", ".join(sorted(discovered - documented)))
    documented_routes = [
        {key: row[key] for key in ("symbol", "path", "methods")} for row in inventory["routes"]
    ]
    if sorted(routes, key=lambda r: r["symbol"]) != sorted(
        documented_routes, key=lambda r: r["symbol"]
    ):
        missing = {r["symbol"] for r in routes} - {r["symbol"] for r in documented_routes}
        raise AuditContractError("Route inventory mismatch: " + ", ".join(sorted(missing)))
    # Discover argparse subcommands independently; flags are included in the inventory
    # and checked against the actual parser in tests.
    cli = ast.parse((root / "src/refrncr/cli.py").read_text())
    commands = {
        ast.literal_eval(n.args[0])
        for n in ast.walk(cli)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_parser"
    }
    if commands != set(inventory["cli_parser_commands"]):
        raise AuditContractError("CLI command inventory mismatch: " + ", ".join(sorted(commands)))
    rows = {row["service"]: row for row in matrix["operations"]}
    if len(rows) != len(matrix["operations"]) or set(rows) != set(OPERATIONS):
        raise AuditContractError("Coverage matrix/operation catalog mismatch")
    for symbol, operation in OPERATIONS.items():
        # Remove a service/method only together with its registration and matrix row.
        parts = symbol.split(".")
        module_length = len(parts) - 1
        while module_length > 1:
            module_path = (root / "src").joinpath(*parts[:module_length])
            path = module_path.with_suffix(".py")
            package_path = module_path / "__init__.py"
            if path.is_file():
                break
            if package_path.is_file():
                path = package_path
                break
            module_length -= 1
        else:
            raise AuditContractError("Registered operation module no longer exists: " + symbol)
        tree = ast.parse(path.read_text())
        if len(parts) - module_length == 2:
            cls = next(
                (
                    node
                    for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == parts[module_length]
                ),
                None,
            )
            nodes = [] if cls is None else cls.body
        else:
            nodes = ast.walk(tree)  # Flask/health functions are composition-local.
        if not any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == parts[-1]
            for node in nodes
        ):
            raise AuditContractError("Registered operation no longer exists: " + symbol)
        row = rows[symbol]
        if row.get("domain_audit") is not (operation.classification == "domain-audited"):
            raise AuditContractError("Coverage domain_audit mismatch: " + symbol)
        if type(row.get("security_logging")) is not bool:
            raise AuditContractError("Coverage security_logging must be boolean: " + symbol)
        if row.get("kind") not in {"read", "extraction", "mutation"}:
            raise AuditContractError("Coverage kind must be read/extraction/mutation: " + symbol)
        if (
            row["classification"] != operation.classification
            or row["exclusion_reason"] != operation.rationale
            or row["events"] != [list(key) for key in operation.events]
        ):
            raise AuditContractError("Coverage contract mismatch: " + symbol)
        expected_context = {
            f"{kind}:{action}": dict(EVENTS[kind, action].context)
            for kind, action in operation.events
        }
        if row["allowed_context"] != expected_context:
            raise AuditContractError("Coverage context mismatch: " + symbol)
        for field in (
            "capability",
            "operation",
            "kind",
            "actor_source",
            "object_identifier",
            "interface_entrypoints",
            "security_handoff",
            "technical_logging",
            "success",
            "validation_failure",
            "authorization_failure",
            "stale_input",
            "no_op",
            "rollback",
            "duplicate_retry",
            "tests",
            "implementation_status",
        ):
            if not row.get(field):
                raise AuditContractError("Missing coverage field " + field + ": " + symbol)
        for reference in row["tests"]:
            filename, name = reference.split("::")
            tree = ast.parse((root / filename).read_text())
            if not any(isinstance(n, ast.FunctionDef) and n.name == name for n in ast.walk(tree)):
                raise AuditContractError("Missing coverage test: " + reference)
    for interface in (
        inventory["routes"] + inventory["cli_commands"] + inventory["implicit_interfaces"]
    ):
        if not interface["operations"]:
            raise AuditContractError("Interface has no audit disposition: " + interface["symbol"])
        require_classified(interface["operations"], OPERATIONS)
        label = interface["symbol"]
        if "path" in interface:
            label += " " + ",".join(interface["methods"]) + " " + interface["path"]
        for symbol, row in rows.items():
            if (symbol in interface["operations"]) != (label in row["interface_entrypoints"]):
                raise AuditContractError("Interface coverage mismatch: " + symbol + " via " + label)
    return len(OPERATIONS), len(routes)
