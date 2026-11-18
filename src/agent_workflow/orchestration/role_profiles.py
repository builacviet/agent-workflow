"""Offline, provider-neutral role profiles and capability preflight contracts.

This module validates trusted configuration and model proposals. It performs no
network, secret resolution, worker launch, or tool execution.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Literal, Mapping
from urllib.parse import urlsplit

Protocol = Literal["chat_completions", "responses"]
ROLE_PHASES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "dev": frozenset({"code"}),
        "qc": frozenset({"plan", "execute"}),
        "tl-reviewer": frozenset({"plan", "code", "qc"}),
    }
)
CAPABILITY_CATALOG_VERSION = "v1"
_IDENTIFIER = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
_SECRET_REF = re.compile(r"^secret://[A-Za-z0-9][A-Za-z0-9_./-]{0,254}$")
_PROTOCOLS = frozenset({"chat_completions", "responses"})


class ProfileError(ValueError):
    """A stable, offline profile or capability validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _model_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and _MODEL_ID.fullmatch(value) is not None
        and not value.endswith("/")
        and "//" not in value
    )


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError("non-finite numbers are not valid JSON")
    return parsed


def _reject_nonfinite_json(_value: str) -> None:
    raise ValueError("non-finite numbers are not valid JSON")


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ProfileError("invalid_schema", "schema object keys must be strings")
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if value is None or type(value) in {bool, int, float, str}:
        return value
    raise ProfileError("invalid_schema", "schema must contain JSON values")


@dataclass(frozen=True)
class RoleProfile:
    provider: str
    model: str
    level: str

    def __post_init__(self) -> None:
        if not _identifier(self.provider) or not _model_identifier(self.model):
            raise ProfileError("invalid_profile", "provider and model are required")
        if self.level != "default" and not _identifier(self.level):
            raise ProfileError("invalid_level", "invalid internal reasoning level")


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    protocol: Protocol
    api_key_ref: str
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str):
            raise ProfileError(
                "invalid_endpoint", "provider endpoint is not approved HTTPS"
            )
        try:
            url = urlsplit(self.base_url)
            valid_url = (
                url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and (url.port is None or 1 <= url.port <= 65535)
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ProfileError(
                "invalid_endpoint", "provider endpoint is not approved HTTPS"
            )
        if self.protocol not in _PROTOCOLS:
            raise ProfileError("invalid_protocol", "unsupported provider protocol")
        if not _SECRET_REF.fullmatch(self.api_key_ref):
            raise ProfileError(
                "invalid_secret_ref", "API key must be a secret reference"
            )
        if not isinstance(self.capabilities, frozenset) or not all(
            _identifier(item) for item in self.capabilities
        ):
            raise ProfileError("invalid_capabilities", "invalid provider capabilities")


@dataclass(frozen=True)
class ModelCapabilities:
    provider: str
    model: str
    protocols: frozenset[Protocol]
    approved_base_urls: frozenset[str]
    capabilities: frozenset[str]
    reasoning_levels: frozenset[str]
    effort_levels: Mapping[Protocol, frozenset[str]]
    default_omits_effort: bool = False

    def __post_init__(self) -> None:
        if not _identifier(self.provider) or not _model_identifier(self.model):
            raise ProfileError("invalid_catalog", "invalid provider/model catalog key")
        if (
            not isinstance(self.protocols, frozenset)
            or not self.protocols
            or not self.protocols <= _PROTOCOLS
        ):
            raise ProfileError("invalid_catalog", "invalid catalog protocols")
        if (
            not isinstance(self.approved_base_urls, frozenset)
            or not self.approved_base_urls
        ):
            raise ProfileError("invalid_catalog", "catalog endpoint allowlist is empty")
        for endpoint in self.approved_base_urls:
            if not isinstance(endpoint, str):
                raise ProfileError("invalid_catalog", "catalog endpoint must use HTTPS")
            try:
                parsed = urlsplit(endpoint)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError
            except ValueError:
                raise ProfileError(
                    "invalid_catalog", "catalog endpoint must use HTTPS"
                ) from None
        if not isinstance(self.capabilities, frozenset) or not all(
            _identifier(item) for item in self.capabilities
        ):
            raise ProfileError("invalid_catalog", "invalid catalog capabilities")
        if not isinstance(self.reasoning_levels, frozenset) or not all(
            _identifier(item) for item in self.reasoning_levels
        ):
            raise ProfileError("invalid_catalog", "invalid catalog reasoning levels")
        if not isinstance(self.default_omits_effort, bool):
            raise ProfileError("invalid_catalog", "invalid default-effort policy")
        if not isinstance(self.effort_levels, Mapping):
            raise ProfileError("invalid_catalog", "invalid protocol effort catalog")
        if any(
            protocol not in self.protocols
            or not isinstance(levels, frozenset)
            or not levels <= self.reasoning_levels
            for protocol, levels in self.effort_levels.items()
        ):
            raise ProfileError("invalid_catalog", "invalid protocol effort levels")
        object.__setattr__(
            self,
            "effort_levels",
            MappingProxyType(
                {key: frozenset(value) for key, value in self.effort_levels.items()}
            ),
        )


@dataclass(frozen=True)
class CapabilityCatalog:
    version: str
    models: Mapping[tuple[str, str], ModelCapabilities]

    def __post_init__(self) -> None:
        if self.version != CAPABILITY_CATALOG_VERSION or not self.models:
            raise ProfileError("invalid_catalog", "versioned model catalog is required")
        normalized = dict(self.models)
        if any(
            not isinstance(model, ModelCapabilities)
            or key != (model.provider, model.model)
            for key, model in normalized.items()
        ):
            raise ProfileError(
                "invalid_catalog", "catalog key does not match model entry"
            )
        object.__setattr__(self, "models", MappingProxyType(normalized))

    def get(self, provider: str, model: str) -> ModelCapabilities:
        try:
            return self.models[(provider, model)]
        except KeyError:
            raise ProfileError(
                "unknown_model", "provider/model is not in trusted catalog"
            ) from None


@dataclass(frozen=True)
class RoleProfiles:
    profiles: Mapping[str, RoleProfile]
    providers: Mapping[str, ProviderConfig]
    catalog: CapabilityCatalog

    def __post_init__(self) -> None:
        profiles = dict(self.profiles)
        providers = dict(self.providers)
        if set(profiles) != set(ROLE_PHASES):
            raise ProfileError(
                "invalid_roles", "dev, qc and tl-reviewer profiles are required"
            )
        if not providers or any(not _identifier(key) for key in providers):
            raise ProfileError("invalid_providers", "provider registry is required")
        if not isinstance(self.catalog, CapabilityCatalog):
            raise ProfileError(
                "invalid_catalog", "trusted capability catalog is required"
            )
        if any(
            not isinstance(provider, ProviderConfig) for provider in providers.values()
        ):
            raise ProfileError("invalid_providers", "invalid provider registry entry")
        for role, profile in profiles.items():
            if (
                not isinstance(profile, RoleProfile)
                or profile.provider not in providers
            ):
                raise ProfileError(
                    "unknown_provider", f"{role} profile has no registered provider"
                )
            provider = providers[profile.provider]
            model = self.catalog.get(profile.provider, profile.model)
            if provider.base_url not in model.approved_base_urls:
                raise ProfileError(
                    "unapproved_endpoint", "endpoint is not in trusted catalog"
                )
            if provider.protocol not in model.protocols:
                raise ProfileError(
                    "protocol_mismatch", "protocol is unsupported by provider/model"
                )
            if not provider.capabilities <= model.capabilities:
                raise ProfileError(
                    "unapproved_capability", "provider declares unapproved capabilities"
                )
            if profile.level == "default" and not model.default_omits_effort:
                raise ProfileError(
                    "unsupported_level", "catalog does not approve omitting effort"
                )
            if (
                profile.level != "default"
                and profile.level not in model.reasoning_levels
            ):
                raise ProfileError(
                    "unsupported_level", "reasoning level is unsupported"
                )
            if (
                profile.level != "default"
                and profile.level
                not in model.effort_levels.get(provider.protocol, frozenset())
            ):
                raise ProfileError(
                    "unsupported_level",
                    "protocol cannot represent this role's reasoning level",
                )
        object.__setattr__(self, "profiles", MappingProxyType(profiles))
        object.__setattr__(self, "providers", MappingProxyType(providers))

    def for_role(self, role: str) -> RoleProfile:
        try:
            return self.profiles[role]
        except KeyError:
            raise ProfileError("unknown_role", "unknown role profile") from None

    def protocol_effort(self, role: str) -> str | None:
        """Return only a protocol-supported effort hint; this is not proof of effect."""
        profile = self.for_role(role)
        provider = self.providers[profile.provider]
        if profile.level == "default":
            return None
        supported = self.catalog.get(profile.provider, profile.model).effort_levels.get(
            provider.protocol, frozenset()
        )
        if profile.level not in supported:
            raise ProfileError(
                "unsupported_level", "protocol cannot represent this level"
            )
        return profile.level


@dataclass(frozen=True)
class StructuredOutput:
    json_schema: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.json_schema, Mapping):
            raise ProfileError("invalid_schema", "a JSON Schema object is required")
        schema = _freeze_json(dict(self.json_schema))
        if not isinstance(schema, Mapping):
            raise ProfileError("invalid_schema", "a JSON Schema object is required")
        _validate_schema(schema)
        object.__setattr__(self, "json_schema", schema)


@dataclass(frozen=True)
class ToolCall:
    name: str
    json_arguments: str
    call_id: str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    json_schema: StructuredOutput


@dataclass(frozen=True)
class TrustedTaskContext:
    """Host-supplied authorization snapshot; untrusted task text cannot mutate it."""

    task_id: str
    role: str
    phase: str
    approved: bool
    allowed_tools: frozenset[str] = frozenset()
    approval_current: bool = True


@dataclass(frozen=True)
class PreflightResult:
    role: str
    provider: str
    model: str
    protocol: Protocol
    status: Literal["POLICY_VALIDATED"]
    launch_ready: bool
    conformance_verified: bool
    reasoning_effort_hint: str | None


def _schema_error(
    value: object, schema: Mapping[str, object], path: str = "$", depth: int = 0
) -> str | None:
    if depth > 32:
        return f"{path}:schema_depth_exceeded"
    expected = schema.get("type")
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: type(item) in {int, float},
        "integer": lambda item: type(item) is int,
        "boolean": lambda item: type(item) is bool,
    }
    if expected not in checks or not checks[expected](value):
        return f"{path}:type_mismatch"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}:enum_mismatch"
    if "const" in schema and value != schema["const"]:
        return f"{path}:const_mismatch"
    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        if not isinstance(properties, Mapping) or not isinstance(
            required, (tuple, list)
        ):
            return f"{path}:invalid_schema"
        for key in required:
            if key not in value:
                return f"{path}.{key}:required"
        if schema.get("additionalProperties", True) is False and set(value) - set(
            properties
        ):
            return f"{path}:additional_property"
        for key, child in properties.items():
            if key in value:
                if not isinstance(child, Mapping):
                    return f"{path}.{key}:invalid_schema"
                issue = _schema_error(value[key], child, f"{path}.{key}", depth + 1)
                if issue:
                    return issue
    if expected == "array" and "items" in schema:
        child = schema["items"]
        if not isinstance(child, Mapping):
            return f"{path}:invalid_schema"
        for index, item in enumerate(value):
            issue = _schema_error(item, child, f"{path}[{index}]", depth + 1)
            if issue:
                return issue
    if expected == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            return f"{path}:min_length"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path}:max_length"
    if expected in {"number", "integer"}:
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}:minimum"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}:maximum"
    return None


def _validate_schema(schema: Mapping[str, object], depth: int = 0) -> None:
    supported = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "enum",
        "const",
    }
    schema_types = {"object", "array", "string", "number", "integer", "boolean"}
    if depth > 32 or set(schema) - supported or schema.get("type") not in schema_types:
        raise ProfileError("invalid_schema", "unsupported JSON Schema structure")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ProfileError("invalid_schema", "properties must be an object")
    for child in properties.values():
        if not isinstance(child, Mapping):
            raise ProfileError("invalid_schema", "property schemas must be objects")
        _validate_schema(child, depth + 1)
    if "required" in schema and (
        not isinstance(schema["required"], tuple)
        or any(not isinstance(item, str) for item in schema["required"])
    ):
        raise ProfileError("invalid_schema", "required must be a string array")
    if (
        "additionalProperties" in schema
        and type(schema["additionalProperties"]) is not bool
    ):
        raise ProfileError("invalid_schema", "additionalProperties must be boolean")
    if "items" in schema:
        if not isinstance(schema["items"], Mapping):
            raise ProfileError("invalid_schema", "items must contain a schema object")
        _validate_schema(schema["items"], depth + 1)
    for key in ("minLength", "maxLength"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ProfileError("invalid_schema", f"{key} must be a nonnegative integer")
    for key in ("minimum", "maximum"):
        if key in schema and (
            type(schema[key]) not in {int, float} or not isfinite(schema[key])
        ):
            raise ProfileError("invalid_schema", f"{key} must be numeric")
    if "enum" in schema and (
        not isinstance(schema["enum"], tuple) or not schema["enum"]
    ):
        raise ProfileError("invalid_schema", "enum must be a nonempty array")


def validate_structured_output(output: str, contract: StructuredOutput) -> object:
    try:
        value = json.loads(
            output,
            parse_constant=_reject_nonfinite_json,
            parse_float=_parse_finite_float,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ProfileError(
            "invalid_json", "structured output is not valid JSON"
        ) from None
    issue = _schema_error(value, contract.json_schema)
    if issue:
        raise ProfileError("schema_validation_failed", issue)
    return value


def normalize_tool_call(
    raw: Mapping[str, object],
    *,
    allowed_tools: frozenset[str],
    tools: Mapping[str, ToolDefinition],
) -> ToolCall:
    if not isinstance(raw, Mapping) or set(raw) != {"name", "arguments", "call_id"}:
        raise ProfileError("invalid_tool_call", "tool-call envelope fields are invalid")
    name, arguments, call_id = raw["name"], raw["arguments"], raw["call_id"]
    if not isinstance(name, str) or name not in allowed_tools:
        raise ProfileError("forbidden_tool", "tool is not allowed for this task")
    if not isinstance(call_id, str) or not _identifier(call_id):
        raise ProfileError("invalid_tool_call", "tool call ID is invalid")
    if isinstance(arguments, Mapping):
        try:
            argument_json = json.dumps(
                arguments, separators=(",", ":"), sort_keys=True, allow_nan=False
            )
        except (TypeError, ValueError):
            raise ProfileError(
                "invalid_arguments", "tool arguments are not JSON values"
            ) from None
        value = dict(arguments)
    elif isinstance(arguments, str):
        try:
            value = json.loads(
                arguments,
                parse_constant=_reject_nonfinite_json,
                parse_float=_parse_finite_float,
            )
        except (ValueError, json.JSONDecodeError):
            raise ProfileError(
                "invalid_arguments", "tool arguments are not valid JSON"
            ) from None
        if not isinstance(value, dict):
            raise ProfileError(
                "invalid_arguments", "tool arguments must be a JSON object"
            )
        argument_json = json.dumps(
            value, separators=(",", ":"), sort_keys=True, allow_nan=False
        )
    else:
        raise ProfileError("invalid_arguments", "tool arguments must be JSON")
    definition = tools.get(name)
    if definition is None:
        raise ProfileError("unknown_tool", "tool has no trusted schema")
    issue = _schema_error(value, definition.json_schema.json_schema)
    if issue:
        raise ProfileError("schema_validation_failed", issue)
    return ToolCall(name, argument_json, call_id)


def preflight(
    config: RoleProfiles,
    context: TrustedTaskContext,
    *,
    required_capabilities: frozenset[str] = frozenset(),
    requested_tools: frozenset[str] = frozenset(),
) -> PreflightResult:
    if not isinstance(config, RoleProfiles):
        raise ProfileError("invalid_config", "trusted role profile config is required")
    if (
        not isinstance(context, TrustedTaskContext)
        or type(context.approved) is not bool
        or not context.approved
        or type(context.approval_current) is not bool
        or not context.approval_current
        or not _identifier(context.task_id)
        or not isinstance(context.allowed_tools, frozenset)
        or not all(_identifier(name) for name in context.allowed_tools)
    ):
        raise ProfileError(
            "approval_required", "an active approved task context is required"
        )
    if (
        not _identifier(context.role)
        or context.role not in ROLE_PHASES
        or not _identifier(context.phase)
        or context.phase not in ROLE_PHASES[context.role]
    ):
        raise ProfileError("role_phase_forbidden", "role/phase is not declared")
    if (
        not isinstance(required_capabilities, frozenset)
        or not all(_identifier(name) for name in required_capabilities)
        or not isinstance(requested_tools, frozenset)
        or not all(_identifier(name) for name in requested_tools)
    ):
        raise ProfileError(
            "invalid_capabilities", "invalid preflight capability request"
        )
    if not requested_tools <= context.allowed_tools:
        raise ProfileError(
            "forbidden_tool", "task context does not allow requested tools"
        )
    profile = config.for_role(context.role)
    provider = config.providers[profile.provider]
    model = config.catalog.get(profile.provider, profile.model)
    required = set(required_capabilities)
    if requested_tools:
        required.add("tool_calling")
    if not required <= provider.capabilities or not required <= model.capabilities:
        raise ProfileError(
            "unsupported_capability", "provider/model lacks required capability"
        )
    if profile.level != "default":
        config.protocol_effort(context.role)
    return PreflightResult(
        role=context.role,
        provider=profile.provider,
        model=profile.model,
        protocol=provider.protocol,
        status="POLICY_VALIDATED",
        # This offline slice validates declarations only; no conformance run
        # or transport exists here, so a provider cannot be launch-ready.
        launch_ready=False,
        conformance_verified=False,
        reasoning_effort_hint=config.protocol_effort(context.role),
    )
