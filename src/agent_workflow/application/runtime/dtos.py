"""Layer: Application
Responsibility: Define runtime request, manifest, plan and result contracts.
Dependencies: Python standard library and Domain validation.
"""

import re
from dataclasses import dataclass
from typing import Literal

from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.workflow_identity import validate_sha

_CREDENTIAL_VALUE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://[^/\s]*@|"
    r"\b(?:bearer\s+|basic\s+|(?:api[_-]?key|access[_-]?token|"
    r"token|secret|password|credential|authorization)\s*[:=])|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|xox[baprs]-|AKIA)[a-z0-9_-]{8,})"
)


def credential_env_name(name: str) -> bool:
    return any(
        marker in name.upper()
        for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
    )


def nonsecret_env_value(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 4096
        and (not value or value.isprintable())
        and _CREDENTIAL_VALUE.search(value) is None
    )


def _identifier(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value))


def _text(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.isprintable()


def _identifiers(values: frozenset[str]) -> bool:
    return isinstance(values, frozenset) and all(map(_identifier, values))


def _combination(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and _identifier(value[0])
        and _text(value[1])
        and (value[2] is None or _identifier(value[2]))
    )


@dataclass(frozen=True)
class RuntimeSpec:
    runtime: str
    provider: str
    model: str
    effort: str | None = None

    def __post_init__(self) -> None:
        if not (
            _identifier(self.runtime)
            and _combination((self.provider, self.model, self.effort))
        ):
            raise BusinessRuleError("invalid runtime, provider, model or effort")


@dataclass(frozen=True)
class RuntimeRequest:
    spec: RuntimeSpec
    role: str
    phase: str
    dispatch_id: str
    input_sha: str
    required_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not (
            isinstance(self.spec, RuntimeSpec)
            and _identifier(self.role)
            and _identifier(self.phase)
            and isinstance(self.dispatch_id, str)
            and re.fullmatch(r"[a-f0-9]{32}", self.dispatch_id)
            and _identifiers(self.required_capabilities)
        ):
            raise BusinessRuleError("invalid runtime request")
        validate_sha(self.input_sha)


@dataclass(frozen=True)
class RuntimeManifest:
    runtime: str
    declared_runtime_version: str
    supported_combinations: frozenset[tuple[str, str, str | None]]
    capabilities: frozenset[str]
    approved_image_id: str | None = None
    approved_plan_env: frozenset[str] = frozenset()
    api_version: int = 1
    approved_plan_values: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self) -> None:
        if not (
            _identifier(self.runtime)
            and _text(self.declared_runtime_version)
            and isinstance(self.supported_combinations, frozenset)
            and self.supported_combinations
            and all(map(_combination, self.supported_combinations))
            and _identifiers(self.capabilities)
            and isinstance(self.approved_plan_env, frozenset)
            and all(
                isinstance(key, str)
                and re.fullmatch(r"WFC_(CODEX_MODEL|PLUGIN_[A-Z][A-Z0-9_]*)", key)
                and not credential_env_name(key)
                for key in self.approved_plan_env
            )
            and isinstance(self.approved_plan_values, frozenset)
            and all(
                isinstance(pair, tuple)
                and len(pair) == 2
                and isinstance(pair[0], str)
                and pair[0] in self.approved_plan_env
                and _text(pair[1])
                and nonsecret_env_value(pair[1])
                for pair in self.approved_plan_values
            )
            and type(self.api_version) is int
        ):
            raise BusinessRuleError("invalid runtime manifest")
        if self.approved_image_id is not None and not (
            isinstance(self.approved_image_id, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", self.approved_image_id)
        ):
            raise BusinessRuleError("invalid approved runtime image ID")


@dataclass(frozen=True)
class RuntimePlan:
    """Arguments inside the image. The runner owns Docker and credential settings."""

    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not (
            isinstance(self.command, tuple)
            and self.command
            and all(map(_text, self.command))
            and self.command[0] not in {"prepare", "publish", "test", "probe"}
        ):
            raise BusinessRuleError("invalid runtime command")
        if not isinstance(self.env, tuple):
            raise BusinessRuleError("invalid runtime environment")
        names = set()
        for pair in self.env:
            if not (
                isinstance(pair, tuple)
                and len(pair) == 2
                and isinstance(pair[0], str)
                and re.fullmatch(r"WFC_(CODEX_MODEL|PLUGIN_[A-Z][A-Z0-9_]*)", pair[0])
                and not credential_env_name(pair[0])
                and _text(pair[1])
                and nonsecret_env_value(pair[1])
                and pair[0] not in names
            ):
                raise BusinessRuleError("invalid runtime environment")
            names.add(pair[0])


@dataclass(frozen=True)
class WorkerResult:
    """Adapter-declared version and independently observed launch identity."""

    request: RuntimeRequest
    declared_runtime_version: str
    exit_code: int = 0
    observed_model: str | None = None
    observed_effort: str | None = None
    telemetry: Literal["UNVERIFIED", "VERIFIED"] = "UNVERIFIED"
    observed_image_id: str | None = None
