"""Executable architecture-policy gate: AST imports, exact legacy debt and runner cycles."""

import ast
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
POLICY = tomllib.loads((ROOT / "architecture" / "policy.toml").read_text(encoding="utf-8"))


def source_files():
    return sorted(SOURCE.rglob("*.py"))


def area_for(path: Path) -> str | None:
    parts = path.relative_to(SOURCE).parts
    if parts[0] == "coder_worker":
        return "coder_worker"
    if len(parts) > 2 and parts[0] == "agent_workflow":
        return parts[1] if parts[1] in POLICY["forbidden_imports"] else None
    return None


def imports(path: Path) -> set[str]:
    """AST catches imports even inside functions and conditional blocks."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module_path = path.relative_to(SOURCE).with_suffix("").parts
    package = ".".join(module_path if path.name == "__init__.py" else module_path[:-1])
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                import importlib.util
                resolved = importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), package
                )
                found.add(resolved)
                if not node.module:
                    found.update(resolved + "." + alias.name for alias in node.names)
            elif node.module:
                found.add(node.module)
                if node.module == "agent_workflow.runners":
                    found.update(node.module + "." + alias.name for alias in node.names)
    return found


def forbidden(area: str, module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in POLICY["forbidden_imports"][area]
    )


def test_no_new_cross_layer_imports_and_no_stale_exceptions():
    actual = {
        (path.relative_to(ROOT).as_posix(), module)
        for path in source_files()
        if (area := area_for(path)) is not None
        for module in imports(path)
        if forbidden(area, module)
    }
    declared = [
        (item["path"], item["module"]) for item in POLICY["legacy_imports"]
    ]
    assert len(declared) == len(set(declared)), "duplicate architecture exemptions"
    assert all(item["reason"].strip() for item in POLICY["legacy_imports"])
    assert actual == set(declared), (
        "Architecture drift. Remove stale exemptions when fixing legacy imports; "
        "new forbidden imports require an architecture decision and MUST NOT be "
        "silently grandfathered. "
        f"Unexpected: {sorted(actual - set(declared))}; "
        f"stale: {sorted(set(declared) - actual)}"
    )


@pytest.mark.parametrize("area", ["domain", "application"])
def test_pure_core_must_not_import_io_or_sql(area):
    external = (
        "sqlite3", "psycopg", "psycopg2", "subprocess", "socket",
        "requests", "httpx", "urllib.request", "docker", "mcp",
    )
    for path in source_files():
        if area_for(path) != area:
            continue
        for module in imports(path):
            assert not any(
                module == name or module.startswith(name + ".") for name in external
            ), f"{path.relative_to(ROOT)} imports infrastructure dependency {module}"


def test_trusted_worker_never_imports_workflow_db_drivers():
    for path in source_files():
        if area_for(path) != "coder_worker":
            continue
        for module in imports(path):
            assert not any(
                module == name or module.startswith(name + ".")
                for name in ("sqlite3", "psycopg", "psycopg2", "sqlalchemy")
            ), f"Worker must not access workflow DB: {path.relative_to(ROOT)}: {module}"


def test_runner_dependency_cycles_have_exact_legacy_inventory():
    paths = list((SOURCE / "agent_workflow" / "runners").glob("*.py"))
    modules = {
        "agent_workflow.runners." + path.stem: path
        for path in paths if path.name != "__init__.py"
    }
    edges = {
        name: {other for other in imports(path) if other in modules}
        for name, path in modules.items()
    }
    cycles = set()

    def visit(start, current, route):
        for target in edges[current]:
            if target == start and len(route) > 1:
                variants = [
                    tuple(route[index:] + route[:index])
                    for index in range(len(route))
                ]
                cycles.add(min(variants))
            elif target not in route and len(route) < len(modules):
                visit(start, target, route + [target])

    for start in sorted(edges):
        visit(start, start, [start])
    expected = {tuple(cycle) for cycle in POLICY["cycles"]["legacy"]}
    assert cycles == expected, (
        f"Runner import-cycle drift. New: {cycles - expected}; "
        f"obsolete allowed cycles: {expected - cycles}"
    )


def test_import_policy_rejects_future_violations():
    assert forbidden("domain", "agent_workflow.storage.postgres")
    assert forbidden("application", "agent_workflow.runners.sandbox")
    assert forbidden("coder_worker", "agent_workflow.storage.repository")
    assert not forbidden("application", "agent_workflow.domain.contracts")
    assert not forbidden("infrastructure", "agent_workflow.application.ports.execution")
