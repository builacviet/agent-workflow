from dataclasses import FrozenInstanceError

import pytest

from agent_workflow.orchestration.role_profiles import (
    CapabilityCatalog,
    ModelCapabilities,
    ProfileError,
    ProviderConfig,
    RoleProfile,
    RoleProfiles,
    StructuredOutput,
    ToolDefinition,
    TrustedTaskContext,
    normalize_tool_call,
    preflight,
    validate_structured_output,
)

URL_A = "https://api.alpha.test/v1"
URL_B = "https://api.beta.test/v1"
BASE_CAPS = frozenset({"json_schema", "tool_calling", "tool_choice"})


def _model(provider: str, model: str, url: str, *, protocol="chat_completions"):
    return ModelCapabilities(
        provider=provider,
        model=model,
        protocols=frozenset({protocol}),
        approved_base_urls=frozenset({url}),
        capabilities=BASE_CAPS,
        reasoning_levels=frozenset({"low", "medium", "high"}),
        effort_levels={protocol: frozenset({"low", "medium", "high"})},
        default_omits_effort=True,
    )


def _config(*, protocol="chat_completions"):
    models = {
        ("alpha", "small"): _model("alpha", "small", URL_A),
        ("beta", "large"): _model("beta", "large", URL_B),
    }
    providers = {
        "alpha": ProviderConfig(URL_A, protocol, "secret://providers/alpha", BASE_CAPS),
        "beta": ProviderConfig(URL_B, protocol, "secret://providers/beta", BASE_CAPS),
    }
    return RoleProfiles(
        profiles={
            "dev": RoleProfile("alpha", "small", "low"),
            "qc": RoleProfile("beta", "large", "medium"),
            "tl-reviewer": RoleProfile("alpha", "small", "default"),
        },
        providers=providers,
        catalog=CapabilityCatalog("v1", models),
    )


def test_three_roles_choose_provider_model_and_level_independently():
    config = _config()
    assert config.for_role("dev") == RoleProfile("alpha", "small", "low")
    assert config.for_role("qc") == RoleProfile("beta", "large", "medium")
    assert config.for_role("tl-reviewer").level == "default"
    assert config.protocol_effort("dev") == "low"
    assert config.protocol_effort("tl-reviewer") is None
    with pytest.raises(FrozenInstanceError):
        config.profiles["dev"].model = "large"


@pytest.mark.parametrize(
    ("profiles", "providers", "catalog", "code"),
    [
        ({}, {}, None, "invalid_roles"),
        (
            {
                "dev": RoleProfile("missing", "x", "low"),
                "qc": RoleProfile("alpha", "x", "low"),
                "tl-reviewer": RoleProfile("alpha", "x", "low"),
            },
            {
                "alpha": ProviderConfig(
                    URL_A, "chat_completions", "secret://p/a", BASE_CAPS
                )
            },
            CapabilityCatalog("v1", {("alpha", "x"): _model("alpha", "x", URL_A)}),
            "unknown_provider",
        ),
    ],
)
def test_required_roles_and_provider_registration(profiles, providers, catalog, code):
    if catalog is None:
        with pytest.raises(ProfileError) as exc:
            RoleProfiles(
                profiles,
                providers,
                CapabilityCatalog("v1", {("alpha", "x"): _model("alpha", "x", URL_A)}),
            )
    else:
        with pytest.raises(ProfileError) as exc:
            RoleProfiles(profiles, providers, catalog)
    assert exc.value.code == code


@pytest.mark.parametrize(
    ("url", "protocol", "secret", "expected"),
    [
        (
            "http://api.alpha.test/v1",
            "chat_completions",
            "secret://p/a",
            "invalid_endpoint",
        ),
        (
            "https://user:pw@api.alpha.test/v1",
            "chat_completions",
            "secret://p/a",
            "invalid_endpoint",
        ),
        (URL_A, "unknown", "secret://p/a", "invalid_protocol"),
        (URL_A, "chat_completions", "sk-inline-secret", "invalid_secret_ref"),
    ],
)
def test_invalid_provider_connection_is_rejected(url, protocol, secret, expected):
    with pytest.raises(ProfileError) as exc:
        ProviderConfig(url, protocol, secret, BASE_CAPS)
    assert exc.value.code == expected


def test_unknown_model_endpoint_and_protocol_mismatch_fail_closed():
    base = _config()
    bad_profiles = dict(base.profiles)
    bad_profiles["dev"] = RoleProfile("alpha", "absent", "low")
    with pytest.raises(ProfileError, match="trusted catalog"):
        RoleProfiles(bad_profiles, base.providers, base.catalog)
    bad_providers = dict(base.providers)
    bad_providers["alpha"] = ProviderConfig(
        URL_B, "chat_completions", "secret://p/a", BASE_CAPS
    )
    with pytest.raises(ProfileError) as endpoint_error:
        RoleProfiles(base.profiles, bad_providers, base.catalog)
    assert endpoint_error.value.code == "unapproved_endpoint"
    bad_providers["alpha"] = ProviderConfig(
        URL_A, "responses", "secret://p/a", BASE_CAPS
    )
    with pytest.raises(ProfileError) as protocol_error:
        RoleProfiles(base.profiles, bad_providers, base.catalog)
    assert protocol_error.value.code == "protocol_mismatch"


def test_invalid_and_unsupported_levels_are_rejected():
    with pytest.raises(ProfileError) as exc:
        RoleProfile("alpha", "small", "not a level")
    assert exc.value.code == "invalid_level"
    config = _config()
    profiles = dict(config.profiles)
    profiles["dev"] = RoleProfile("alpha", "small", "reasoning-x")
    # Syntactically valid levels still have to be catalogued.
    with pytest.raises(ProfileError) as unsupported:
        RoleProfiles(profiles, config.providers, config.catalog)
    assert unsupported.value.code == "unsupported_level"


def test_default_level_requires_explicit_catalog_effort_omission_policy():
    model = _model("alpha", "small", URL_A)
    model_without_default = ModelCapabilities(
        provider=model.provider,
        model=model.model,
        protocols=model.protocols,
        approved_base_urls=model.approved_base_urls,
        capabilities=model.capabilities,
        reasoning_levels=model.reasoning_levels,
        effort_levels=model.effort_levels,
        default_omits_effort=False,
    )
    config = _config()
    profiles = dict(config.profiles)
    profiles["dev"] = RoleProfile("alpha", "small", "default")
    with pytest.raises(ProfileError) as exc:
        RoleProfiles(
            profiles,
            config.providers,
            CapabilityCatalog(
                "v1",
                {
                    ("alpha", "small"): model_without_default,
                    ("beta", "large"): _model("beta", "large", URL_B),
                },
            ),
        )
    assert exc.value.code == "unsupported_level"


def test_capability_preflight_requires_active_matching_role_and_allowed_tools():
    config = _config()
    with pytest.raises(ProfileError) as absent:
        preflight(config, TrustedTaskContext("TASK-1", "dev", "code", False))
    assert absent.value.code == "approval_required"
    with pytest.raises(ProfileError) as stale:
        preflight(
            config,
            TrustedTaskContext("TASK-1", "dev", "code", True, approval_current=False),
        )
    assert stale.value.code == "approval_required"
    with pytest.raises(ProfileError) as phase:
        preflight(config, TrustedTaskContext("TASK-1", "dev", "execute", True))
    assert phase.value.code == "role_phase_forbidden"
    context = TrustedTaskContext(
        "TASK-1", "dev", "code", True, frozenset({"read_file"})
    )
    with pytest.raises(ProfileError) as forbidden:
        preflight(config, context, requested_tools=frozenset({"shell"}))
    assert forbidden.value.code == "forbidden_tool"
    passed = preflight(config, context, requested_tools=frozenset({"read_file"}))
    assert passed.status == "POLICY_VALIDATED"
    assert passed.launch_ready is False
    assert passed.conformance_verified is False


def test_provider_must_declare_each_required_capability():
    config = _config()
    providers = dict(config.providers)
    providers["alpha"] = ProviderConfig(
        URL_A, "chat_completions", "secret://p/a", frozenset({"json_schema"})
    )
    revised = RoleProfiles(config.profiles, providers, config.catalog)
    with pytest.raises(ProfileError) as exc:
        preflight(
            revised,
            TrustedTaskContext("TASK-2", "dev", "code", True),
            required_capabilities=frozenset({"tool_calling"}),
        )
    assert exc.value.code == "unsupported_capability"


def test_provider_cannot_claim_catalog_unapproved_capabilities():
    config = _config()
    providers = dict(config.providers)
    providers["alpha"] = ProviderConfig(
        URL_A,
        "chat_completions",
        "secret://p/a",
        BASE_CAPS | frozenset({"host_shell"}),
    )
    with pytest.raises(ProfileError) as exc:
        RoleProfiles(config.profiles, providers, config.catalog)
    assert exc.value.code == "unapproved_capability"


def test_structured_output_schema_validation_and_normalized_errors():
    contract = StructuredOutput(
        {
            "type": "object",
            "properties": {"summary": {"type": "string", "minLength": 1}},
            "required": ["summary"],
            "additionalProperties": False,
        }
    )
    assert validate_structured_output('{"summary":"done"}', contract) == {
        "summary": "done"
    }
    for value, code in [
        ("not-json", "invalid_json"),
        ('{"extra":true}', "schema_validation_failed"),
    ]:
        with pytest.raises(ProfileError) as exc:
            validate_structured_output(value, contract)
        assert exc.value.code == code
    with pytest.raises(TypeError):
        contract.json_schema["type"] = "string"
    with pytest.raises(ProfileError) as malformed_schema:
        StructuredOutput({"type": "object", "properties": {"x": {"type": "made_up"}}})
    assert malformed_schema.value.code == "invalid_schema"


def test_tool_call_envelope_checks_allowlist_json_and_schema():
    tool = ToolDefinition(
        "write_doc",
        StructuredOutput(
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            }
        ),
    )
    definitions = {"write_doc": tool}
    normalized = normalize_tool_call(
        {
            "name": "write_doc",
            "arguments": '{"path":"docs/readme.md"}',
            "call_id": "call-1",
        },
        allowed_tools=frozenset({"write_doc"}),
        tools=definitions,
    )
    assert normalized.json_arguments == '{"path":"docs/readme.md"}'
    assert normalized.call_id == "call-1"
    invalid_calls = [
        ({"name": "shell", "arguments": "{}", "call_id": "call-2"}, "forbidden_tool"),
        (
            {"name": "write_doc", "arguments": "{bad", "call_id": "call-2"},
            "invalid_arguments",
        ),
        (
            {"name": "write_doc", "arguments": "{}", "call_id": "call-2"},
            "schema_validation_failed",
        ),
        (
            {"name": "write_doc", "arguments": "{}", "call_id": "bad id"},
            "invalid_tool_call",
        ),
    ]
    for envelope, code in invalid_calls:
        with pytest.raises(ProfileError) as exc:
            normalize_tool_call(
                envelope, allowed_tools=frozenset({"write_doc"}), tools=definitions
            )
        assert exc.value.code == code


def test_responses_protocol_is_a_single_protocol_contract_and_effort_mismatch_fails():
    model = _model("alpha", "small", URL_A, protocol="responses")
    model = ModelCapabilities(
        provider=model.provider,
        model=model.model,
        protocols=model.protocols,
        approved_base_urls=model.approved_base_urls,
        capabilities=model.capabilities,
        reasoning_levels=model.reasoning_levels,
        effort_levels={},
        default_omits_effort=True,
    )
    config = RoleProfiles(
        profiles={
            "dev": RoleProfile("alpha", "small", "low"),
            "qc": RoleProfile("alpha", "small", "default"),
            "tl-reviewer": RoleProfile("alpha", "small", "default"),
        },
        providers={
            "alpha": ProviderConfig(URL_A, "responses", "secret://p/a", BASE_CAPS)
        },
        catalog=CapabilityCatalog("v1", {("alpha", "small"): model}),
    )
    with pytest.raises(ProfileError) as exc:
        config.protocol_effort("dev")
    assert exc.value.code == "unsupported_level"


def test_untrusted_prompt_cannot_change_trusted_role_or_permission_snapshot():
    config = _config()
    context = TrustedTaskContext("TASK-3", "qc", "plan", True, frozenset({"read_doc"}))
    # Task/prompt text is intentionally not an input to profile or authorization selection.
    hostile_prompt = {"role": "dev", "allowed_tools": ["shell"], "provider": "alpha"}
    result = preflight(config, context)
    assert result.role == "qc"
    assert config.for_role("qc").provider == "beta"
    assert context.allowed_tools == frozenset({"read_doc"})
    assert hostile_prompt["allowed_tools"] == ["shell"]


def test_unknown_tool_without_schema_is_rejected():
    with pytest.raises(ProfileError) as exc:
        normalize_tool_call(
            {"name": "missing", "arguments": "{}", "call_id": "call-3"},
            allowed_tools=frozenset({"missing"}),
            tools={},
        )
    assert exc.value.code == "unknown_tool"
