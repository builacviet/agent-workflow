"""Pure offline mappings for generic Chat Completions and Responses protocols.

This module translates trusted V1-02C contracts. It never resolves secrets,
performs I/O, dispatches tools, or selects a role from model output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal, cast

from agent_workflow.orchestration.role_profiles import (
    ProfileError,
    RoleProfiles,
    StructuredOutput,
    ToolCall,
    ToolDefinition,
    TrustedTaskContext,
    normalize_tool_call,
    preflight,
    validate_structured_output,
)

MAX_INSTRUCTIONS_CHARS = 64_000
MAX_RESPONSE_BYTES = 1_000_000
MAX_TOOL_CALLS = 32
_TOOL_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
Protocol = Literal["chat_completions", "responses"]


@dataclass(frozen=True)
class ProtocolRequest:
    """A deterministic request body and relative protocol path."""

    protocol: Protocol
    path: str
    body: Mapping[str, object]


@dataclass(frozen=True)
class NormalizedResponse:
    """Validated model output; tool calls are data and are never executed."""

    structured_output: object | None
    tool_calls: tuple[ToolCall, ...]


def _fail(code: str, message: str) -> None:
    raise ProfileError(code, message)


def _json_schema(contract: StructuredOutput) -> dict[str, object]:
    """Return a JSON-compatible copy of the immutable canonical schema."""

    def thaw(value: object) -> object:
        if isinstance(value, Mapping):
            return {key: thaw(value[key]) for key in sorted(value)}
        if isinstance(value, (tuple, list)):
            return [thaw(item) for item in value]
        return value

    return cast(dict[str, object], thaw(contract.json_schema))


def _validate_tools(
    tools: Mapping[str, ToolDefinition] | None,
    allowed_tools: frozenset[str],
) -> tuple[ToolDefinition, ...]:
    if tools is None:
        tools = {}
    if not isinstance(tools, Mapping) or not isinstance(allowed_tools, frozenset):
        _fail("invalid_tool_call", "trusted tool definitions are required")
    if not all(
        isinstance(name, str) and _TOOL_NAME.fullmatch(name) for name in allowed_tools
    ):
        _fail("invalid_tool_call", "trusted allowed-tool set is invalid")
    if set(tools) - allowed_tools:
        _fail("forbidden_tool", "tool definition is not allowed for this task")
    if any(
        not isinstance(name, str)
        or not isinstance(definition, ToolDefinition)
        or name != definition.name
        or not _TOOL_NAME.fullmatch(definition.name)
        or not isinstance(definition.json_schema, StructuredOutput)
        for name, definition in tools.items()
    ):
        _fail("invalid_tool_call", "trusted tool definitions are invalid")
    if len(tools) > MAX_TOOL_CALLS:
        _fail("payload_too_large", "too many tool definitions")
    return tuple(tools[name] for name in sorted(tools))


def _tool_choice(choice: str | None, tools: tuple[ToolDefinition, ...]) -> str | None:
    if not tools:
        if choice is not None:
            _fail("unsupported_capability", "tool choice requires tool definitions")
        return None
    if choice is not None and not isinstance(choice, str):
        _fail("invalid_tool_call", "tool choice must be a string")
    selected = choice or "auto"
    if selected not in {"auto", "none", "required", *(tool.name for tool in tools)}:
        _fail("forbidden_tool", "selected tool choice is not allowlisted")
    if selected == "none":
        return selected
    if selected not in {"auto", "required"} and selected not in {
        tool.name for tool in tools
    }:
        _fail("forbidden_tool", "selected tool choice is not allowlisted")
    return selected


def encode_request(
    config: RoleProfiles,
    context: TrustedTaskContext,
    instructions: str,
    *,
    structured_output: StructuredOutput | None = None,
    tools: Mapping[str, ToolDefinition] | None = None,
    tool_choice: str | None = None,
) -> ProtocolRequest:
    """Encode a selected V1-02C profile into one generic protocol request."""
    if not isinstance(instructions, str) or not instructions.strip():
        _fail("invalid_request", "task instructions must be nonempty text")
    if len(instructions) > MAX_INSTRUCTIONS_CHARS:
        _fail("payload_too_large", "task instructions exceed the size limit")
    if structured_output is not None and not isinstance(
        structured_output, StructuredOutput
    ):
        _fail("invalid_schema", "structured output contract is invalid")
    allowed = (
        context.allowed_tools
        if isinstance(context, TrustedTaskContext)
        else frozenset()
    )
    definitions = _validate_tools(tools, allowed)
    selected_choice = _tool_choice(tool_choice, definitions)
    required = set()
    if structured_output is not None:
        required.add("json_schema")
    if definitions:
        required.update({"tool_calling", "tool_choice"})
    result = preflight(
        config,
        context,
        required_capabilities=frozenset(required),
        requested_tools=frozenset(tool.name for tool in definitions),
    )
    provider = config.providers[result.provider]
    if provider.protocol not in {"chat_completions", "responses"}:
        _fail("invalid_protocol", "unsupported protocol")

    parameters = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": "",
                "parameters": _json_schema(tool.json_schema),
                "strict": True,
            },
        }
        for tool in definitions
    ]
    body: dict[str, object]
    if result.protocol == "chat_completions":
        body = {
            "model": result.model,
            "messages": [{"role": "system", "content": instructions}],
        }
        if structured_output is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "task_output",
                    "strict": True,
                    "schema": _json_schema(structured_output),
                },
            }
        if parameters:
            body["tools"] = parameters
            body["tool_choice"] = _chat_tool_choice(selected_choice)
        if result.reasoning_effort_hint is not None:
            body["reasoning_effort"] = result.reasoning_effort_hint
        path = "/chat/completions"
    else:
        body = {"model": result.model, "instructions": instructions}
        if structured_output is not None:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "task_output",
                    "strict": True,
                    "schema": _json_schema(structured_output),
                }
            }
        if parameters:
            body["tools"] = [
                parameter["function"] | {"type": "function"} for parameter in parameters
            ]
            body["tool_choice"] = _responses_tool_choice(selected_choice)
        if result.reasoning_effort_hint is not None:
            body["reasoning"] = {"effort": result.reasoning_effort_hint}
        path = "/responses"
    try:
        request_size = len(
            json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeError):
        _fail("invalid_request", "request contains unsupported JSON values")
    if request_size > MAX_RESPONSE_BYTES:
        _fail("payload_too_large", "encoded request exceeds the size limit")
    return ProtocolRequest(result.protocol, path, body)


def _chat_tool_choice(choice: str | None) -> object:
    if choice in {None, "auto", "none", "required"}:
        return choice or "auto"
    return {"type": "function", "function": {"name": choice}}


def _responses_tool_choice(choice: str | None) -> object:
    if choice in {None, "auto", "none", "required"}:
        return choice or "auto"
    return {"type": "function", "name": choice}


def _load_json(text: str) -> object:
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        _fail("invalid_json", "provider response is not valid UTF-8 text")
    if len(encoded) > MAX_RESPONSE_BYTES:
        _fail("payload_too_large", "provider response exceeds the size limit")
    try:
        return json.loads(
            text,
            parse_constant=lambda _value: _fail("invalid_json", "invalid JSON number"),
            parse_float=lambda value: _finite_float(value),
            object_pairs_hook=_unique_object,
        )
    except ProfileError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("invalid_json", "provider response is not valid JSON")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not isfinite(parsed):
        _fail("invalid_json", "provider response contains a nonfinite number")
    return parsed


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("ambiguous_response", "duplicate JSON object keys are unsupported")
        result[key] = value
    return result


def normalize_response(
    protocol: Protocol,
    response_json: str,
    *,
    structured_output: StructuredOutput | None = None,
    tools: Mapping[str, ToolDefinition] | None = None,
    allowed_tools: frozenset[str] = frozenset(),
) -> NormalizedResponse:
    """Normalize one complete protocol response using trusted V1-02C contracts."""
    if not isinstance(protocol, str) or protocol not in {
        "chat_completions",
        "responses",
    }:
        _fail("invalid_protocol", "unsupported protocol")
    if not isinstance(response_json, str):
        _fail("invalid_json", "provider response must be JSON text")
    if structured_output is not None and not isinstance(
        structured_output, StructuredOutput
    ):
        _fail("invalid_schema", "structured output contract is invalid")
    if not isinstance(allowed_tools, frozenset):
        _fail("invalid_tool_call", "trusted allowed-tool set is required")
    tool_definitions = _validate_tools(tools, allowed_tools)
    trusted_tools = {tool.name: tool for tool in tool_definitions}
    raw = _load_json(response_json)
    if not isinstance(raw, Mapping):
        _fail("invalid_response", "provider response must be an object")
    if protocol == "chat_completions":
        output, calls = _chat_response(raw)
    else:
        output, calls = _responses_response(raw)
    if output is not None and calls:
        _fail("ambiguous_response", "response cannot mix text and tool calls")
    if output is None and not calls:
        _fail("invalid_response", "provider response contains no supported output")
    if len(calls) > MAX_TOOL_CALLS:
        _fail("payload_too_large", "provider returned too many tool calls")
    normalized_calls: list[ToolCall] = []
    seen_ids: set[str] = set()
    for call in calls:
        call_id = call.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            _fail("invalid_tool_call", "tool call ID is required")
        if call_id in seen_ids:
            _fail("invalid_tool_call", "tool call IDs must be unique")
        seen_ids.add(call_id)
        normalized_calls.append(
            normalize_tool_call(
                call,
                allowed_tools=allowed_tools,
                tools=trusted_tools,
            )
        )
    normalized_output = None
    if output is not None:
        if structured_output is None:
            _fail("unsupported_output", "text output has no trusted schema")
        normalized_output = validate_structured_output(output, structured_output)
    return NormalizedResponse(normalized_output, tuple(normalized_calls))


def _chat_response(
    raw: Mapping[str, object],
) -> tuple[str | None, list[dict[str, object]]]:
    if set(raw) - {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "system_fingerprint",
    }:
        _fail("protocol_mismatch", "response is not a supported Chat Completions shape")
    if "object" in raw and raw["object"] != "chat.completion":
        _fail("protocol_mismatch", "response object is not Chat Completions")
    choices = raw.get("choices")
    if (
        not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], Mapping)
    ):
        _fail("invalid_response", "exactly one complete choice is required")
    choice = choices[0]
    if set(choice) - {"index", "message", "finish_reason", "logprobs"}:
        _fail("protocol_mismatch", "unsupported Chat Completions choice shape")
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or finish_reason not in {
        "stop",
        "tool_calls",
    }:
        _fail("invalid_response", "incomplete or ambiguous choice is unsupported")
    message = choice.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        _fail("invalid_response", "assistant message is required")
    if set(message) - {"role", "content", "tool_calls"}:
        _fail("protocol_mismatch", "unsupported or ambiguous assistant message")
    content = message.get("content")
    raw_calls = message.get("tool_calls", [])
    if content is not None and not isinstance(content, str):
        _fail("protocol_mismatch", "unsupported message content shape")
    if not isinstance(raw_calls, list):
        _fail("protocol_mismatch", "tool_calls must be an array")
    calls = []
    for item in raw_calls:
        if not isinstance(item, Mapping) or item.get("type") != "function":
            _fail("protocol_mismatch", "unsupported tool-call shape")
        if set(item) != {"id", "type", "function"}:
            _fail("protocol_mismatch", "unsupported tool-call fields")
        function = item.get("function")
        if not isinstance(function, Mapping) or set(function) != {"name", "arguments"}:
            _fail("protocol_mismatch", "unsupported function-call shape")
        calls.append(
            {
                "name": function.get("name"),
                "arguments": function.get("arguments"),
                "call_id": item.get("id"),
            }
        )
    if calls and choice["finish_reason"] != "tool_calls":
        _fail("protocol_mismatch", "tool calls require a tool_calls finish reason")
    if not calls and choice["finish_reason"] != "stop":
        _fail("protocol_mismatch", "tool_calls finish reason requires tool calls")
    return (content if content else None), calls


def _responses_response(
    raw: Mapping[str, object],
) -> tuple[str | None, list[dict[str, object]]]:
    if set(raw) - {
        "id",
        "object",
        "created_at",
        "status",
        "model",
        "output",
        "usage",
        "error",
        "incomplete_details",
        "metadata",
    }:
        _fail("protocol_mismatch", "response is not a supported Responses shape")
    if "object" in raw and raw["object"] != "response":
        _fail("protocol_mismatch", "response object is not a Responses result")
    if raw.get("status") != "completed":
        _fail("invalid_response", "only completed Responses outputs are supported")
    output = raw.get("output")
    if not isinstance(output, list):
        _fail("protocol_mismatch", "Responses output must be an array")
    texts: list[str] = []
    calls: list[dict[str, object]] = []
    message_count = 0
    for item in output:
        if not isinstance(item, Mapping):
            _fail("protocol_mismatch", "unsupported Responses output item")
        if item.get("type") == "message":
            message_count += 1
            if message_count > 1:
                _fail(
                    "ambiguous_response", "multiple Responses messages are unsupported"
                )
            if set(item) - {"id", "type", "role", "status", "content"}:
                _fail("protocol_mismatch", "unsupported or ambiguous Responses message")
            if item.get("role") != "assistant" or item.get("status") != "completed":
                _fail("protocol_mismatch", "unsupported Responses message")
            content = item.get("content")
            if not isinstance(content, list) or not content:
                _fail("protocol_mismatch", "message content must be an array")
            for part in content:
                if (
                    not isinstance(part, Mapping)
                    or part.get("type") != "output_text"
                    or not isinstance(part.get("text"), str)
                    or set(part) - {"type", "text", "annotations"}
                ):
                    _fail("protocol_mismatch", "unsupported Responses content part")
                texts.append(part["text"])
        elif item.get("type") == "function_call":
            if set(item) - {
                "id",
                "type",
                "status",
                "name",
                "arguments",
                "call_id",
            } or not {
                "type",
                "name",
                "arguments",
                "call_id",
            } <= set(item):
                _fail("protocol_mismatch", "unsupported function-call fields")
            if "status" in item and item["status"] != "completed":
                _fail("invalid_response", "incomplete function calls are unsupported")
            calls.append(
                {
                    "name": item.get("name"),
                    "arguments": item.get("arguments"),
                    "call_id": item.get("call_id"),
                }
            )
        else:
            _fail("protocol_mismatch", "unsupported Responses output item")
    if len(texts) > 1:
        _fail("ambiguous_response", "multiple text parts are unsupported")
    return (texts[0] if texts else None), calls
