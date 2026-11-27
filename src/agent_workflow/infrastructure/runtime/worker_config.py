"""Layer: Infrastructure
Responsibility: Validate local Worker configuration, paths and credential separation.
Dependencies: Domain rules and local Worker, runtime and GitHub adapters.
"""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol

from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.workflow_identity import REPOSITORY
from agent_workflow.infrastructure.github.app_token import ControllerAppToken
from agent_workflow.infrastructure.github.client import GitHubClient
from agent_workflow.infrastructure.runtime.registry import (
    RuntimeRegistry,
    runtime_spec_from_config,
)
from agent_workflow.infrastructure.runtime.workspace import is_linked

DENIED = {".git", ".secret", ".runtime", ".codex", ".env", "AGENTS.md"}
WINDOWS_DEVICES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(
            part.casefold() in {name.casefold() for name in DENIED}
            or part.casefold().startswith(".env")
            or part.endswith((" ", "."))
            or part.split(".")[0].casefold() in WINDOWS_DEVICES
            for part in path.parts
        )
        or path.suffix.lower() in {".pem", ".key"}
    ):
        raise BusinessRuleError("unsafe coder path")
    return value


def independent_path(path: Path, *, directory: bool = False) -> None:
    forbidden = (
        "agent_workflow_v2",
        "workflow_console_v2",
        "workflow-console-v2",
    )
    for candidate in (path, path.resolve()):
        directories = candidate.parts if directory else candidate.parts[:-1]
        if any(part.casefold().startswith(forbidden) for part in directories):
            raise BusinessRuleError("v2 paths are forbidden")


def validate_config(config: dict, *, require_database: bool = True) -> None:
    if config.get("repository") != REPOSITORY or not config.get("tasks"):
        raise BusinessRuleError("explicit repository and task allowlist required")
    for key in (
        ("database", "pem", "scratch") if require_database else ("pem", "scratch")
    ):
        path = Path(config[key])
        if not path.is_absolute():
            raise BusinessRuleError("absolute operator paths required")
        independent_path(path, directory=key == "scratch")
    if config.get("runtime", "codex") == "codex":
        profile = Path(config["seccomp_profile"])
        if not profile.is_absolute() or not profile.is_file() or profile.is_symlink():
            raise BusinessRuleError("absolute independent seccomp profile required")
        independent_path(profile)
        if hashlib.sha256(profile.read_bytes()).hexdigest() != config["seccomp_sha256"]:
            raise BusinessRuleError("seccomp profile digest mismatch")
        if config.get("codex_auth_volume") != "techlead-review-poc_codex_auth":
            raise BusinessRuleError("dedicated POC Codex auth volume required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*", config["image"]):
        raise BusinessRuleError("invalid runtime image")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        raise BusinessRuleError("operator-selected model required")
    roots = set()
    for task in config["tasks"].values():
        root = Path(task["worktree"])
        if not root.is_absolute() or is_linked(root) or not root.is_dir():
            raise BusinessRuleError("dedicated canonical worktree required")
        if str(root.resolve()) != str(root) or root.resolve() in roots:
            raise BusinessRuleError("duplicate or noncanonical worktree")
        if not (root / ".git").is_file():
            raise BusinessRuleError("task requires linked Git worktree")
        independent_path(root, directory=True)
        roots.add(root.resolve())
        if not task.get("write_paths"):
            raise BusinessRuleError("explicit write path allowlist required")
        for prefix in task["write_paths"]:
            safe_path(prefix)
    if (
        type(config.get("timeout_seconds", 1800)) is not int
        or not 60 <= config.get("timeout_seconds", 1800) <= 3600
    ):
        raise BusinessRuleError("invalid timeout")


class _WorkerAppIdentity(Protocol):
    app_id: int
    installation_id: int
    pem: Path


def validate_dispatch_worker_config(
    config: dict,
    registry: RuntimeRegistry,
    worker_app: Callable[[int, int, Path], _WorkerAppIdentity],
) -> None:
    """Validate local settings only: no database, Docker or GitHub requests."""
    try:
        validate_config(config, require_database=False)
        # The approved registry is shared with the per-claim Worker.
        registry.validate_selection(
            runtime_spec_from_config(config),
            role="dev",
            phase="code",
            required_capabilities=frozenset({"workspace-write"}),
        )
        # Reuse the trusted Worker's credential checks without authenticating.
        worker = worker_app(
            config["app_id"], config["installation_id"], Path(config["pem"])
        )
        gate = config["controller_github_app"]
        controller = ControllerAppToken(
            app_id=gate["app_id"],
            installation_id=gate["installation_id"],
            pem_path=gate["pem"],
            request_json=GitHubClient().request_json,
        )
        controller_pem = Path(gate["pem"]).resolve(strict=True)
        if (
            not controller_pem.is_file()
            or worker.pem == controller_pem
            or worker.app_id == controller.app_id
            or worker.installation_id == controller.installation_id
        ):
            raise BusinessRuleError(
                "separate Controller and Worker credentials required"
            )
        roots = [Path(config["scratch"]).resolve()] + [
            Path(task["worktree"]).resolve() for task in config["tasks"].values()
        ]
        if any(
            pem.is_relative_to(root)
            for pem in (worker.pem, controller_pem)
            for root in roots
        ):
            raise BusinessRuleError("App credentials must remain outside Worker paths")
    except (KeyError, TypeError, ValueError, OSError):
        raise InvalidApplicationRequestError(
            "invalid PostgreSQL dispatch Worker configuration"
        ) from None
