"""Static architecture guards for production module boundaries."""

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "refrncr"
DOMAIN_PACKAGES_WITH_TYPED_ERRORS = (
    "identity",
    "authorization",
    "datasets",
    "versions",
    "imports",
    "hierarchies",
)
GENERAL_ERROR_TYPES = {
    "AuthorizationError",
    "ConflictError",
    "NotFoundError",
    "ValidationError",
}
ALLOWED_DYNAMIC_IMPORTS = {
    ("errors.py", "'refrncr.hierarchies.errors'"),
    ("errors.py", "'refrncr.imports.errors'"),
    ("errors.py", "'refrncr.datasets.errors'"),
    ("hierarchies/__init__.py", "'refrncr.hierarchies.imports'"),
    ("hierarchies/__init__.py", "'refrncr.hierarchies.service'"),
    ("imports/__init__.py", "'refrncr.imports.service'"),
    ("versions/__init__.py", "'refrncr.versions.service'"),
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _private_functions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("_")
        and not node.name.startswith("__")
    }


def _from_module(path: Path, consumer: str, imported: ast.ImportFrom) -> str:
    if imported.level == 0:
        return imported.module or ""
    package = consumer.split(".") if path.name == "__init__.py" else consumer.split(".")[:-1]
    parent = package[: len(package) - (imported.level - 1)]
    return ".".join([*parent, *(imported.module or "").split(".")]).rstrip(".")


def test_production_modules_do_not_use_private_functions_from_other_modules():
    modules = {}
    trees = {}
    for path in SOURCE_ROOT.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        module = _module_name(path)
        modules[module] = _private_functions(tree)
        trees[path] = tree

    violations = []
    for path, tree in trees.items():
        consumer = _module_name(path)
        module_aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_module = _from_module(path, consumer, node)
                for imported in node.names:
                    if (
                        imported_module in modules
                        and imported.name in modules[imported_module]
                        and imported_module != consumer
                    ):
                        violations.append(
                            f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno} imports "
                            f"private function {imported_module}.{imported.name}"
                        )
                    child_module = f"{imported_module}.{imported.name}"
                    if child_module in modules:
                        module_aliases[imported.asname or imported.name] = child_module
            elif isinstance(node, ast.Import):
                for imported in node.names:
                    if imported.name in modules:
                        module_aliases[imported.asname or imported.name.rsplit(".", 1)[-1]] = (
                            imported.name
                        )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            module = module_aliases.get(node.value.id)
            if module != consumer and module is not None and node.attr in modules[module]:
                violations.append(
                    f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno} uses private "
                    f"function {module}.{node.attr}"
                )

    assert violations == [], "Private functions crossed module boundaries:\n" + "\n".join(
        violations
    )


def test_layers_above_persistence_do_not_hide_dependencies_in_functions():
    violations = []
    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT)
        if (
            "migrations" in relative.parts
            or relative.parts[0] == "db"
            or path.name == "models.py"
            or "persistence" in path.stem
        ):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ):
            for node in ast.walk(function):
                if isinstance(node, ast.Import | ast.ImportFrom):
                    violations.append(
                        f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno} imports "
                        f"inside {function.name}"
                    )

    assert violations == [], "Function-local imports hide dependencies:\n" + "\n".join(violations)


def test_dynamic_imports_are_limited_to_documented_compatibility_facades():
    discovered = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            is_import_module = (
                isinstance(function, ast.Name) and function.id == "import_module"
            ) or (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "importlib"
                and function.attr == "import_module"
            )
            if is_import_module:
                discovered.add((str(path.relative_to(SOURCE_ROOT)), ast.unparse(node.args[0])))

    assert discovered == ALLOWED_DYNAMIC_IMPORTS


def test_services_do_not_hold_a_private_composition_backdoor():
    violations = []
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_community_services":
                violations.append(f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno}")
            elif isinstance(node, ast.Constant) and node.value == "_community_services":
                violations.append(f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno}")

    assert violations == [], "Private composition backdoor found:\n" + "\n".join(violations)


def test_split_domain_packages_do_not_raise_general_error_categories():
    violations = []
    for package in DOMAIN_PACKAGES_WITH_TYPED_ERRORS:
        for path in (SOURCE_ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            imported_names = {
                imported.asname or imported.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for imported in node.names
                if imported.name in GENERAL_ERROR_TYPES
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                    continue
                raised = node.exc.func
                if isinstance(raised, ast.Name) and (
                    raised.id in GENERAL_ERROR_TYPES or raised.id in imported_names
                ):
                    violations.append(
                        f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno} raises "
                        f"general category {raised.id}"
                    )
                elif isinstance(raised, ast.Attribute) and raised.attr in GENERAL_ERROR_TYPES:
                    violations.append(
                        f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno} raises "
                        f"general category {raised.attr}"
                    )

    assert violations == [], "Domain packages raised general error categories:\n" + "\n".join(
        violations
    )


def test_domain_packages_do_not_import_persistence_adapters():
    violations = []
    for package in DOMAIN_PACKAGES_WITH_TYPED_ERRORS:
        for path in (SOURCE_ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                modules = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [name.name for name in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for module in modules:
                    if module == "refrncr.persistence" or module.startswith("refrncr.persistence."):
                        violations.append(
                            f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{node.lineno} "
                            f"imports {module}"
                        )

    assert violations == [], "Domain packages imported persistence adapters:\n" + "\n".join(
        violations
    )
