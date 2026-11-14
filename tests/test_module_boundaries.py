"""Static architecture guards for production module boundaries."""

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "refrncr"


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
