"""Executable architecture-policy gate: AST imports, exact legacy debt and runner cycles."""

import ast
import re
import tomllib
from collections import Counter
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
POLICY = tomllib.loads(
    (ROOT / "architecture" / "policy.toml").read_text(encoding="utf-8")
)


def source_files():
    return sorted(SOURCE.rglob("*.py"))


def area_for(path: Path) -> str | None:
    parts = path.relative_to(SOURCE).parts
    if (
        len(parts) > 2
        and parts[0] == "agent_workflow"
        and parts[1] == "workers"
        and parts[2] == "trusted_git"
    ):
        return "trusted_git"
    if len(parts) > 2 and parts[0] == "agent_workflow":
        return parts[1] if parts[1] in POLICY["forbidden_imports"] else None
    return None


def imports(path: Path) -> set[str]:
    """AST catches imports even inside functions and conditional blocks."""
    source = path.read_text(encoding="utf-8")
    module_path = path.relative_to(SOURCE).with_suffix("").parts
    package = ".".join(module_path if path.name == "__init__.py" else module_path[:-1])
    return imports_from_source(source, package)


def imports_from_source(source: str, package: str) -> set[str]:
    tree = ast.parse(source)
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
            else:
                resolved = node.module or ""
            if resolved:
                found.add(resolved)
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported = f"{resolved}.{alias.name}" if resolved else alias.name
                protected_alias = any(
                    prefix == imported or prefix.startswith(imported + ".")
                    for forbidden_imports in POLICY["forbidden_imports"].values()
                    for prefix in forbidden_imports
                )
                if protected_alias:
                    # `from package import child` can name a parent of a
                    # forbidden nested package. Record that protected alias
                    # too because AST cannot expose later attribute use.
                    found.update(
                        prefix
                        for forbidden_imports in POLICY["forbidden_imports"].values()
                        for prefix in forbidden_imports
                        if prefix == imported or prefix.startswith(imported + ".")
                    )
                if node.module == "agent_workflow.runners":
                    found.add(imported)
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
    declared = [(item["path"], item["module"]) for item in POLICY["legacy_imports"]]
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
        "sqlite3",
        "psycopg",
        "psycopg2",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "urllib.request",
        "docker",
        "mcp",
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
        if area_for(path) != "trusted_git":
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
        for path in paths
        if path.name != "__init__.py"
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
                    tuple(route[index:] + route[:index]) for index in range(len(route))
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
    assert forbidden("domain", "agent_workflow.workers.trusted_git.main")
    assert forbidden("application", "agent_workflow.workers.trusted_git.app_auth")
    assert forbidden("trusted_git", "agent_workflow.storage.repository")
    assert not forbidden("application", "agent_workflow.domain.contracts")
    assert not forbidden("infrastructure", "agent_workflow.application.ports.execution")


def test_webhook_core_and_adapter_preserve_layer_boundaries():
    core_paths = [
        SOURCE / "agent_workflow" / "application" / "webhook" / "ports.py",
        SOURCE / "agent_workflow" / "application" / "webhook" / "receive_webhook.py",
        SOURCE / "agent_workflow" / "domain" / "webhook" / "inbox.py",
    ]
    for path in core_paths:
        area = "application" if "application" in path.parts else "domain"
        for module in imports(path):
            assert not forbidden(area, module), (
                f"{path.relative_to(ROOT)} imports {module}"
            )
            assert not module.startswith(("sqlalchemy", "psycopg"))

    receiver = imports(
        SOURCE / "agent_workflow" / "interfaces" / "webhook" / "github_receiver.py"
    )
    assert not any(
        module.startswith(
            (
                "agent_workflow.storage",
                "agent_workflow.integrations",
                "sqlalchemy",
                "psycopg",
            )
        )
        for module in receiver
    )

    # Webhook infrastructure adapter owns its transaction over a supplied
    # factory-scoped Session provider, never a Composition UoW.
    repo_path = (
        SOURCE
        / "agent_workflow"
        / "infrastructure"
        / "database"
        / "repositories"
        / "webhook_inbox_repository.py"
    )
    repo_tree = ast.parse(repo_path.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Name) and node.id == "PostgresFactory"
        for node in ast.walk(repo_tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"commit", "rollback"}
        for node in ast.walk(repo_tree)
    )
    repository = next(
        node
        for node in repo_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "SqlAlchemyWebhookInboxRepository"
    )
    init = next(
        node
        for node in repository.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assert len(init.args.args) == 2
    assert init.args.args[1].arg == "session_scope"
    assert isinstance(init.args.args[1].annotation, ast.Name)
    assert init.args.args[1].annotation.id == "SessionScope"
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "begin"
        for node in ast.walk(repository)
    )
    assert "sqlalchemy.orm" in imports(repo_path)


def test_ci_reader_repository_keep_session_and_transaction_boundaries():
    base = SOURCE / "agent_workflow" / "infrastructure" / "database" / "repositories"
    reader_path = base / "ci_state_reader.py"
    repository_path = base / "ci_state_repository.py"
    reader_tree = ast.parse(reader_path.read_text(encoding="utf-8"))
    repository_tree = ast.parse(repository_path.read_text(encoding="utf-8"))

    for path, tree in (
        (reader_path, reader_tree),
        (repository_path, repository_tree),
    ):
        assert "PostgresFactory" not in {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }, f"{path.relative_to(ROOT)} must receive a Session provider"
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "repository_session"
            for node in ast.walk(tree)
        ), f"{path.relative_to(ROOT)} must open a fresh scoped Session"

    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "commit"
        for node in ast.walk(reader_tree)
    ), f"{reader_path.relative_to(ROOT)} must not commit"
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "commit"
        for node in ast.walk(repository_tree)
    ), f"{repository_path.relative_to(ROOT)} must use Session.begin()"
    assert {
        node.name for node in ast.walk(reader_tree) if isinstance(node, ast.FunctionDef)
    } >= {"tracked", "previous_failure", "approved", "pending_events"}
    assert {
        node.name
        for node in ast.walk(repository_tree)
        if isinstance(node, ast.FunctionDef)
    } >= {"track", "record", "mark_processed"}


def test_webhook_dependency_assembly_is_separate_from_http_bootstrap():
    composition = SOURCE / "agent_workflow" / "composition"
    entrypoint = ast.parse(
        (composition / "webhook_entrypoint.py").read_text(encoding="utf-8")
    )
    entrypoint_names = {
        node.name for node in entrypoint.body if isinstance(node, ast.ClassDef)
    }
    assert "_ScopedWebhookInbox" not in entrypoint_names
    assert "create_webhook_use_case" in {
        alias.name
        for node in entrypoint.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not {
        node.module for node in entrypoint.body if isinstance(node, ast.ImportFrom)
    } & {
        "agent_workflow.infrastructure.database.repositories.webhook_inbox_repository",
        "agent_workflow.domain.ci_evidence",
        "agent_workflow.domain.webhook.inbox",
    }

    dependencies = ast.parse(
        (composition / "webhook_deps.py").read_text(encoding="utf-8")
    )
    assert "_ScopedWebhookInbox" not in ast.dump(dependencies)
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SqlAlchemyWebhookInboxRepository"
        for node in ast.walk(dependencies)
    )
    factory_call = next(
        node
        for node in ast.walk(dependencies)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SqlAlchemyWebhookInboxRepository"
    )
    assert any(
        keyword.arg == "session_scope"
        and isinstance(keyword.value, ast.Attribute)
        and keyword.value.attr == "get_scoped_session"
        for keyword in factory_call.keywords
    )
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "create_webhook_use_case"
        for node in dependencies.body
    )


@pytest.mark.parametrize(
    "source",
    [
        "from agent_workflow.workers import trusted_git",
        "from agent_workflow import workers",
    ],
)
def test_import_policy_rejects_nested_worker_parent_aliases(source):
    modules = imports_from_source(source, "agent_workflow.domain")
    assert any(forbidden("domain", module) for module in modules)


def test_import_policy_allows_composition_to_import_worker():
    path = SOURCE / "agent_workflow" / "composition" / "postgres_dispatch_entrypoint.py"
    modules = imports(path)
    worker_imports = {
        module for module in modules if module.startswith("agent_workflow.workers")
    }
    assert "agent_workflow.workers.trusted_git.app_auth" in worker_imports
    assert area_for(path) is None


def test_nested_trusted_git_package_is_classified():
    path = SOURCE / "agent_workflow" / "workers" / "trusted_git" / "main.py"
    assert area_for(path) == "trusted_git"


# The inventory is audited against approved main@376aacf2. Lines are diagnostics,
# never exemptions. Repeated identical violations are counted per symbol.
# admin.py was clean at the approved base and still participates in the scan.
_LEGACY_R2_AST_INVENTORY = """\
src/agent_workflow/bootstrap/mcp_runtime.py|inline-import|postgres_workflow_provider|agent_workflow.composition.postgres_mcp.postgres_mcp_service|1
src/agent_workflow/controller.py|inline-import|<module>|agent_workflow.orchestration.controller|1
src/agent_workflow/controller.py|inline-import|<module>|agent_workflow.orchestration.controller.main|1
src/agent_workflow/domain/contracts.py|core-import|<module>|pydantic.BaseModel|1
src/agent_workflow/domain/contracts.py|core-import|<module>|pydantic.ConfigDict|1
src/agent_workflow/domain/contracts.py|core-import|<module>|pydantic.Field|1
src/agent_workflow/domain/contracts.py|core-import|<module>|pydantic.StrictStr|1
src/agent_workflow/domain/contracts.py|domain-base|StrictModel|pydantic.BaseModel|1
src/agent_workflow/domain/service.py|core-import|<module>|agent_workflow.integrations.github.REPOSITORY|1
src/agent_workflow/domain/service.py|core-import|<module>|agent_workflow.integrations.github.WorkflowError|1
src/agent_workflow/domain/service.py|core-import|<module>|agent_workflow.integrations.github.canonical_repository|1
src/agent_workflow/domain/service.py|core-import|<module>|agent_workflow.integrations.github.validate_sha|1
src/agent_workflow/domain/service.py|core-import|<module>|agent_workflow.paths.database_path|1
src/agent_workflow/domain/service.py|core-import|<module>|agent_workflow.storage.repository.SQLiteWorkflowRepository|1
src/agent_workflow/domain/service.py|core-import|<module>|agent_workflow.storage.repository.WorkflowRepository|1
src/agent_workflow/integrations/github_app_checks.py|inline-import|ControllerAppToken.__call__|jwt|1
src/agent_workflow/mcp/security.py|inline-import|authorize_context|mcp.server.auth.middleware.auth_context.get_access_token|1
src/agent_workflow/mcp/security.py|inline-import|authorize_techlead|mcp.server.auth.middleware.auth_context.get_access_token|1
src/agent_workflow/mcp/server.py|inline-import|create_mcp_server|agent_workflow.mcp.auth0_oauth.Auth0TokenVerifier|1
src/agent_workflow/mcp/server.py|inline-import|create_mcp_server|agent_workflow.mcp.auth0_oauth.load_auth0_config|1
src/agent_workflow/mcp/server.py|inline-import|create_mcp_server|mcp.server.auth.settings.AuthSettings|1
src/agent_workflow/mcp/server.py|inline-import|create_mcp_server|pydantic.AnyHttpUrl|1
src/agent_workflow/orchestration/controller.py|inline-import|_ci_operator|agent_workflow.integrations.ci_controller.ControllerCiCoordinator|1
src/agent_workflow/orchestration/controller.py|inline-import|_ci_operator|agent_workflow.integrations.github_app_checks.ControllerAppToken|1
src/agent_workflow/orchestration/controller.py|inline-import|_ci_operator|agent_workflow.integrations.github_app_checks.ControllerChecksClient|1
src/agent_workflow/orchestration/controller.py|inline-import|_ci_operator|agent_workflow.storage.ci_state.ControllerCiState|1
src/agent_workflow/orchestration/controller.py|inline-import|main|agent_workflow.composition.postgres_ci_entrypoint.run_postgres_ci_once|1
src/agent_workflow/orchestration/controller.py|inline-import|main|agent_workflow.composition.postgres_ci_entrypoint.run_postgres_ci_watch|1
src/agent_workflow/orchestration/controller.py|inline-import|main|agent_workflow.composition.postgres_dispatch_entrypoint.run_postgres_dispatch_once|1
src/agent_workflow/orchestration/controller.py|inline-import|main|agent_workflow.runners.coder_runtime.validate_config|1
src/agent_workflow/orchestration/controller.py|inline-import|operate|agent_workflow.runners.coder_runtime.CoderRuntime|1
src/agent_workflow/orchestration/controller.py|inline-import|process_lock|fcntl|1
src/agent_workflow/orchestration/controller.py|inline-import|process_lock|msvcrt|1
src/agent_workflow/runners/coder_runtime.py|inline-import|CoderRuntime._validated_result|agent_workflow.integrations.github.validate_sha|1
src/agent_workflow/runners/coder_runtime.py|inline-import|_SqliteCiTracking.track|agent_workflow.storage.ci_state.ControllerCiState|1
src/agent_workflow/runners/sandbox.py|inline-import|SandboxManager.run|agent_workflow.runners.coder_runtime.run_container|1
src/agent_workflow/runners/sandbox.py|inline-import|_docker|agent_workflow.runners.coder_runtime.docker_command|1
src/agent_workflow/runners/sandbox.py|inline-import|_workspace_bind|agent_workflow.runners.coder_runtime.is_linked|1
src/agent_workflow/sandbox_state_paths.py|inline-import|_windows_fixed_drive|ctypes|1
src/agent_workflow/server.py|inline-import|<module>|agent_workflow.mcp.server|1
src/agent_workflow/server.py|inline-import|<module>|agent_workflow.mcp.server.main|1
src/agent_workflow/workers/trusted_git/main.py|inline-import|git_auth|base64|1
"""
LEGACY_R2_AST = tuple(
    (path, rule, symbol, detail, int(occurrence))
    for entry in _LEGACY_R2_AST_INVENTORY.splitlines()
    for path, rule, symbol, detail, occurrence in [entry.split("|")]
)


CORE_IMPORTS = {
    "pydantic",
    "sqlalchemy",
    "fastapi",
    "httpx",
    "aio_pika",
    "importlib",
    "agent_workflow.app_config",
}
UNSAFE_CALLS = {"print", "open", "eval", "exec", "__import__"}
USE_CASE_TYPES = {
    "Any",
    "AbstractContextManager",
    "ContextManager",
    "Session",
    "Engine",
    "PostgresFactory",
}
TASK_USE_CASE = "src/agent_workflow/application/task/use_cases/authorize_task.py"
TASK_COMPOSITION = "src/agent_workflow/composition/task_deps.py"
SESSION_UOW = "src/agent_workflow/infrastructure/database/unit_of_work.py"


def _qualified(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_qualified(node.value)}.{node.attr}"
    return ""


def _imports_in(node, package):
    if isinstance(node, ast.Import):
        return [
            (
                alias.name,
                alias.asname or alias.name.split(".")[0],
                alias.name if alias.asname else alias.name.split(".")[0],
            )
            for alias in node.names
        ]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            module = resolve_name("." * node.level + module, package)
        return [
            (
                f"{module}.{alias.name}",
                alias.asname or alias.name,
                f"{module}.{alias.name}",
            )
            for alias in node.names
        ]
    return []


def _qualified_alias(node, aliases):
    name = _qualified(node)
    first, dot, rest = name.partition(".")
    return aliases.get(first, first) + dot + rest


def _module_try(parent, parents):
    # Optional dependencies can be imported in a module-level import guard.
    return (
        isinstance(parent, ast.Try)
        and isinstance(parents.get(parent), ast.Module)
        and any(
            _qualified(handler.type) in {"ImportError", "ModuleNotFoundError"}
            for handler in parent.handlers
        )
    )


def _annotations(args):
    return [
        arg
        for arg in (
            *args.posonlyargs,
            *args.args,
            args.vararg,
            *args.kwonlyargs,
            args.kwarg,
        )
        if arg and arg.arg != "self"
    ]


def _usecases(tree):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith("UseCase")
    ]


def _method(cls, name):
    return next(
        (
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )


def r2_violations(path, source):
    tree = ast.parse(source, filename=path)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    parts = Path(path).parts
    area = parts[2] if len(parts) > 3 else ""
    core = area in {"domain", "application"}
    opaque_core = area in {"domain", "application", "interfaces"}
    package = ".".join(parts[1:-1])
    aliases = {
        bound: qualified
        for node in ast.walk(tree)
        for _, bound, qualified in _imports_in(node, package)
    }
    found = []
    occurrences = Counter()

    def record(node, rule, detail):
        enclosing = [node.name] if isinstance(node, ast.ClassDef) else []
        parent = parents.get(node)
        while parent is not None:
            if isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                enclosing.append(parent.name)
            parent = parents.get(parent)
        symbol = ".".join(reversed(enclosing)) or "<module>"
        key = (path, rule, symbol, detail)
        occurrences[key] += 1
        found.append(((*key, occurrences[key]), node.lineno))

    for node in sorted(
        ast.walk(tree),
        key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)),
    ):
        for target, _, _ in _imports_in(node, package):
            if path == "src/agent_workflow/admin.py" and (
                target
                in {
                    "sqlite3",
                    "sqlalchemy",
                    "agent_workflow.domain.service",
                    "agent_workflow.integrations.github",
                    "agent_workflow.storage",
                    "agent_workflow.infrastructure",
                }
                or target.startswith(
                    (
                        "sqlite3.",
                        "sqlalchemy.",
                        "agent_workflow.domain.service.",
                        "agent_workflow.integrations.github.",
                        "agent_workflow.storage.",
                        "agent_workflow.infrastructure.",
                    )
                )
            ):
                record(node, "admin-legacy-import", target)
            parent = parents[node]
            typing_only = (
                isinstance(parent, ast.If)
                and isinstance(parents.get(parent), ast.Module)
                and node in parent.body
                and _qualified_alias(parent.test, aliases) == "typing.TYPE_CHECKING"
            )
            if not (
                isinstance(parent, ast.Module)
                or typing_only
                or (_module_try(parent, parents) and node in parent.body)
            ):
                record(node, "inline-import", target)
            if core and (
                any(
                    target == name or target.startswith(name + ".")
                    for name in CORE_IMPORTS
                )
                or forbidden(area, target)
            ):
                record(node, "core-import", target)
            if opaque_core and (
                target.split(".")[0] in {"sqlalchemy", "psycopg", "psycopg2"}
                or target == "agent_workflow.infrastructure.database"
                or target.startswith("agent_workflow.infrastructure.database.")
                or (
                    area == "interfaces"
                    and (
                        target == "agent_workflow.infrastructure"
                        or target.startswith("agent_workflow.infrastructure.")
                    )
                )
            ):
                record(node, "db-adapter-import", target)
        if opaque_core:
            identifier = ""
            if isinstance(node, ast.Name):
                identifier = node.id
            elif isinstance(node, ast.Attribute):
                identifier = node.attr
            elif isinstance(node, ast.arg):
                identifier = node.arg
            elif isinstance(node, ast.alias):
                identifier = node.name
                if node.asname and any(
                    fragment in node.asname.casefold()
                    for fragment in ("postgres", "sqlalchemy")
                ):
                    identifier = node.asname
            elif isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                identifier = node.name
            if identifier and any(
                fragment in identifier.casefold()
                for fragment in ("postgres", "sqlalchemy")
            ):
                record(node, "db-adapter-name", identifier)
        if core and isinstance(node, ast.Call):
            target = _qualified_alias(node.func, aliases)
            if target.removeprefix("builtins.") in UNSAFE_CALLS or target in {
                "importlib.import_module",
                "importlib.util.module_from_spec",
            }:
                record(node, "core-call", target)
        if (
            area == "interfaces" or path == "src/agent_workflow/admin.py"
        ) and isinstance(node, ast.Call):
            target = _qualified_alias(node.func, aliases)
            if target in {
                "__import__",
                "builtins.__import__",
                "importlib.import_module",
            }:
                rule = (
                    "interface-dynamic-import"
                    if area == "interfaces"
                    else "admin-dynamic-import"
                )
                record(node, rule, target)
        if (
            path == "src/agent_workflow/admin.py"
            and isinstance(node, ast.Attribute)
            and node.attr == "postgres"
        ):
            record(node, "admin-backend-selector", node.attr)
        if isinstance(node, ast.ClassDef):
            bases = [_qualified_alias(base, aliases) for base in node.bases]
            if area == "domain":
                for base in bases:
                    if (
                        base.startswith(("pydantic.", "sqlalchemy."))
                        or base.split(".")[-1] in {"BaseModel", "DeclarativeBase"}
                        or (
                            base.startswith("agent_workflow.infrastructure.")
                            and base.endswith(".Base")
                        )
                    ):
                        record(node, "domain-base", base)
            if (
                area == "application"
                and parts[-1] == "ports.py"
                and "typing.Protocol" in bases
                and not re.match(r"^I[A-Z]", node.name)
            ):
                record(node, "protocol-name", node.name)

    def blocked_annotation(annotation):
        for part in ast.walk(annotation):
            if _qualified_alias(part, aliases).split(".")[-1] in USE_CASE_TYPES:
                return True
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                try:
                    forward = ast.parse(part.value, mode="eval").body
                except SyntaxError:
                    continue
                if not isinstance(forward, ast.Constant) and blocked_annotation(
                    forward
                ):
                    return True
        return False

    if area == "application":
        for cls in _usecases(tree):
            init = _method(cls, "__init__")
            if init is None:
                record(cls, "use-case-init", cls.name)
                continue
            for param in _annotations(init.args):
                annotation = param.annotation
                if (
                    param.arg == "open_scope"
                    or annotation is None
                    or blocked_annotation(annotation)
                ):
                    record(param, "use-case-dependency", param.arg)
    return dict(found)


def _ratchet(actual, expected):
    new = actual.keys() - expected
    stale = expected - actual.keys()
    if new or stale:
        details = [
            f"{path}:{actual[key]} {rule} in {symbol}: {detail} (occurrence {occurrence})"
            for key in sorted(new)
            for path, rule, symbol, detail, occurrence in [key]
        ]
        details += [f"Stale AST exemption; prune {item}" for item in sorted(stale)]
        raise AssertionError("\n".join(details))


def test_r2_ast_ratchet_has_no_new_violations_or_stale_exemptions():
    actual = {}
    for path in source_files():
        actual.update(
            r2_violations(
                path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")
            )
        )
    assert len(LEGACY_R2_AST) == len(set(LEGACY_R2_AST))
    _ratchet(actual, set(LEGACY_R2_AST))


def _source_tree(path):
    return ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)


def test_every_r2_use_case_has_unit_tests():
    tests = ROOT / "tests" / "unit"
    for path in (SOURCE / "agent_workflow" / "application").rglob("*.py"):
        if not _usecases(_source_tree(path)):
            continue
        candidates = list(tests.rglob(f"test_{path.stem}.py"))
        assert any(_has_tests(_source_tree(test)) for test in candidates), (
            f"{path}: expected test_{path.stem}.py with a test under tests/unit"
        )


def _has_tests(tree):
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )


def _task_di(tree, path):
    (cls,) = [node for node in _usecases(tree) if node.name == "AuthorizeTaskUseCase"]
    init = _method(cls, "__init__")
    assert init is not None, f"{path}:{cls.lineno}: explicit constructor required"
    params = {arg.arg: _qualified(arg.annotation) for arg in _annotations(init.args)}
    assert (
        params.get("uow") == "IUnitOfWork"
        and params.get("repository") == "ITaskAuthorizationRepository"
        and "open_scope" not in params
    ), (
        f"{path}:{init.lineno}: inject IUnitOfWork and ITaskAuthorizationRepository directly"
    )
    execute = _method(cls, "execute")
    assert execute and any(
        isinstance(node, ast.With)
        and any(
            _qualified(item.context_expr) == "self._uow"
            and not isinstance(item.optional_vars, (ast.Tuple, ast.List))
            for item in node.items
        )
        for node in ast.walk(execute)
    ), f"{path}:{cls.lineno}: execute must enter its injected UoW"


def test_task_authorization_wiring_is_explicit():
    _task_di(_source_tree(TASK_USE_CASE), TASK_USE_CASE)
    cls = next(
        node
        for node in _source_tree(SESSION_UOW).body
        if isinstance(node, ast.ClassDef) and node.name == "SqlAlchemyUnitOfWork"
    )
    ctor = _method(cls, "__init__")
    params = {arg.arg: _qualified(arg.annotation) for arg in _annotations(ctor.args)}
    assert (
        params.get("session") == "Session" and "PostgresFactory" not in params.values()
    ), f"{SESSION_UOW}:{ctor.lineno}: receive a Session"
    tree = _source_tree(TASK_COMPOSITION)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            assert node.value not in ("PG_RUNTIME_DSN", "PGHOSTADDR", "PGSERVICE"), (
                f"{TASK_COMPOSITION}:{node.lineno}: setting validation belongs in Config"
            )
        if isinstance(node, (ast.Name, ast.Attribute)):
            assert _qualified(node).split(".")[-1] not in {
                "environ",
                "getenv",
                "urlsplit",
                "validate_pg_runtime_dsn",
            }, f"{TASK_COMPOSITION}:{node.lineno}: setting validation belongs in Config"
    scope = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "create_authorize_task_use_case"
    )
    for block in (node for node in ast.walk(scope) if isinstance(node, ast.With)):
        for item in block.items:
            if not isinstance(item.context_expr, ast.Call) or not _qualified(
                item.context_expr.func
            ).endswith(".get_scoped_session"):
                continue
            session = _qualified(item.optional_vars)
            assignments = {
                _qualified(node.value.func): (
                    _qualified(node.targets[0]),
                    [_qualified(arg) for arg in node.value.args],
                )
                for node in block.body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
            }
            unit, unit_args = assignments.get("SqlAlchemyUnitOfWork", ("", []))
            repo, repo_args = assignments.get(
                "SqlAlchemyTaskAuthorizationRepository", ("", [])
            )
            yields = [
                node.value
                for node in block.body
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Yield)
            ]
            assert session and unit and repo and unit_args == repo_args == [session], (
                f"{TASK_COMPOSITION}:{block.lineno}: share the injected Session"
            )
            assert any(
                isinstance(node.value, ast.Call)
                and _qualified(node.value.func) == "AuthorizeTaskUseCase"
                and [_qualified(arg) for arg in node.value.args] == [unit, repo]
                for node in yields
            ), (
                f"{TASK_COMPOSITION}:{block.lineno}: inject the scoped UoW and repository"
            )
            return
    pytest.fail(f"{TASK_COMPOSITION}:{scope.lineno}: scoped Session assembly missing")


@pytest.mark.parametrize(
    ("area", "module", "source", "rule"),
    [
        (
            "domain",
            "entity",
            "import pydantic as p\nclass Record(p.BaseModel): pass",
            "domain-base",
        ),
        (
            "domain",
            "entity",
            "from sqlalchemy.orm import DeclarativeBase as Parent\nclass Record(Parent): pass",
            "domain-base",
        ),
        ("domain", "entity", "from builtins import eval as run\nrun('x')", "core-call"),
        (
            "domain",
            "entity",
            "from importlib import import_module as load\nload('x')",
            "core-call",
        ),
        (
            "application",
            "use_case",
            "from typing import Any as Unsafe\nclass BadUseCase:\n def __init__(self, dependency: list[Unsafe]): pass",
            "use-case-dependency",
        ),
        (
            "application",
            "use_case",
            "class BadUseCase:\n def __init__(self, dependency: 'ContextManager[Session]'): pass",
            "use-case-dependency",
        ),
        (
            "application",
            "use_case",
            "class BadUseCase:\n def __init__(self, open_scope): pass",
            "use-case-dependency",
        ),
        (
            "application",
            "ports",
            "from typing import Protocol as Contract\nclass Store(Contract): pass",
            "protocol-name",
        ),
        (
            "application",
            "use_case",
            "from agent_workflow import infrastructure",
            "core-import",
        ),
        ("application", "use_case", "class BadUseCase: pass", "use-case-init"),
        ("application", "use_case", "def load():\n import json", "inline-import"),
        (
            "interfaces",
            "cli",
            "from agent_workflow.infrastructure.database import session_factory",
            "db-adapter-import",
        ),
        (
            "interfaces",
            "cli",
            "import agent_workflow.infrastructure.database.unit_of_work as storage",
            "db-adapter-import",
        ),
        (
            "interfaces",
            "cli",
            "from agent_workflow import infrastructure",
            "db-adapter-import",
        ),
        (
            "interfaces",
            "cli",
            "from .ports import PostgresGateway as Gateway",
            "db-adapter-name",
        ),
        (
            "domain",
            "exceptions",
            "from sqlalchemy.exc import OperationalError as BackendError",
            "db-adapter-import",
        ),
        (
            "application",
            "exceptions",
            "from agent_workflow.infrastructure.database.unit_of_work import PostgresUnavailableError as BackendError",
            "db-adapter-import",
        ),
        (
            "interfaces",
            "cli",
            "from psycopg import OperationalError as BackendError",
            "db-adapter-import",
        ),
        (
            "interfaces",
            "cli",
            "from importlib import import_module as load\nload('psycopg')",
            "interface-dynamic-import",
        ),
        (
            "interfaces",
            "cli",
            "def use_session(factory):\n postgres_client = factory()",
            "db-adapter-name",
        ),
        (
            "application",
            "use_case",
            "class SqlAlchemyStore: pass",
            "db-adapter-name",
        ),
        (
            "domain",
            "entity",
            "def from_Postgres(row): pass",
            "db-adapter-name",
        ),
    ],
)
def test_r2_ast_rejects_bad_source(area, module, source, rule):
    path = f"src/agent_workflow/{area}/{module}.py"
    with pytest.raises(AssertionError, match=rf"{path}:\d+ {rule}"):
        _ratchet(r2_violations(path, source), set())


def test_r2_ast_legacy_ratchet_rejects_added_and_stale_imports():
    path = "src/agent_workflow/admin.py"
    source = "def old():\n import json"
    expected = {(path, "inline-import", "old", "json", 1)}
    _ratchet(r2_violations(path, source), expected)
    with pytest.raises(AssertionError, match="admin-dynamic-import in provision"):
        dynamic = (
            "\ndef provision():\n __import__('agent_workflow.composition.task_deps')"
        )
        _ratchet(r2_violations(path, source + dynamic), expected)
    with pytest.raises(AssertionError, match="inline-import in new"):
        _ratchet(r2_violations(path, source + "\ndef new():\n import json"), expected)
    with pytest.raises(AssertionError, match="Stale AST exemption"):
        _ratchet(r2_violations(path, "def old(): pass"), expected)


def test_r2_ast_allows_typing_dependencies_and_plain_dtos():
    path = "src/agent_workflow/application/example/ports.py"
    source = """from typing import TYPE_CHECKING as CHECK, Protocol, Callable
if CHECK:
    from .dtos import Example
class IReader(Protocol): pass
class PlainDTO: pass
class GetUseCase:
    def __init__(self, reader: IReader, clock: Callable[[], float]): pass
"""
    _ratchet(r2_violations(path, source), set())
    assert _has_tests(ast.parse("class Suite:\n def test_work(self): pass"))
    assert not _has_tests(ast.parse("def fixture(): pass"))


@pytest.mark.parametrize(
    "source",
    [
        "from agent_workflow.domain.service import default_service",
        "from agent_workflow.domain.service import WorkflowService as Engine",
        "import sqlite3 as database",
        "from agent_workflow.storage.repository import SQLiteWorkflowRepository",
        "from agent_workflow.infrastructure.database import session_factory",
        "from agent_workflow.integrations.github import WorkflowError",
        "from agent_workflow.integrations import github as legacy_adapter",
    ],
)
def test_admin_rejects_legacy_and_database_imports(source):
    path = "src/agent_workflow/admin.py"
    with pytest.raises(AssertionError, match="admin-legacy-import"):
        _ratchet(r2_violations(path, source), set())


def test_admin_rejects_backend_selection_from_deprecated_flag():
    path = "src/agent_workflow/admin.py"
    with pytest.raises(AssertionError, match="admin-backend-selector"):
        _ratchet(r2_violations(path, "if args.postgres: select_backend()"), set())


def test_r2_ast_allows_cli_flag_without_concrete_names():
    path = "src/agent_workflow/interfaces/cli/example.py"
    source = 'BACKEND_FLAG = "--postgres"\ndef execute(command):\n return command'
    _ratchet(r2_violations(path, source), set())


def test_r2_ast_rejects_callback_task_di():
    source = "class AuthorizeTaskUseCase:\n def __init__(self, open_scope): pass"
    with pytest.raises(
        AssertionError, match="inject IUnitOfWork and ITaskAuthorizationRepository"
    ):
        _task_di(ast.parse(source), TASK_USE_CASE)


def test_ci_composition_does_not_expose_postgres_state_bundle():
    """CI wiring injects focused operations, never a DB-specific state facade."""
    paths = (
        SOURCE / "agent_workflow" / "composition" / "ci_deps.py",
        SOURCE / "agent_workflow" / "composition" / "postgres_controller.py",
        SOURCE / "agent_workflow" / "composition" / "worker_deps.py",
    )
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        classes = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        assert "PostgresCiStateDependencies" not in identifiers | classes
        assert "CiStateAdapter" not in identifiers | classes
    worker = ast.parse(paths[2].read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "ci_state"
        for node in ast.walk(worker)
    )
