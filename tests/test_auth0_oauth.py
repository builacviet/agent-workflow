"""Auth0 resource-server tests: synthetic RSA keys; never call the real provider."""

import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from my_wfc.auth0_oauth import Auth0TokenVerifier, load_auth0_config


@pytest.fixture
def auth_config(monkeypatch):
    monkeypatch.setenv("WFC_POC_NO_AUTH", "0")
    monkeypatch.setenv("WFC_AUTH0_ISSUER", "https://example.us.auth0.com/")
    monkeypatch.setenv("WFC_AUTH0_ALLOWED_SUB", "auth0|owner")
    monkeypatch.delenv("WFC_OAUTH_EXPECTED_CLIENT_ID", raising=False)
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", "https://chatgpt.com/oauth/client.json")
    monkeypatch.setenv("WFC_AUTH0_SCOPE", "wfc:workflow")
    return load_auth0_config("test.example.org")


@pytest.fixture
def jwt_case(auth_config):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = dict(
        iss=auth_config.issuer,
        sub=auth_config.allowed_sub,
        aud=auth_config.resource,
        exp=now + 300,
        iat=now - 1,
        scope=auth_config.scope,
        azp="https://chatgpt.com/oauth/client.json",
    )
    verifier = Auth0TokenVerifier(auth_config)
    calls = []

    async def fake_jwks():
        calls.append(1)
        return {"key-1": key.public_key()}

    verifier._fetch_jwks = fake_jwks

    def sign(**overrides):
        return jwt.encode(
            {**claims, **overrides}, key,
            algorithm="RS256", headers={"kid": "key-1"},
        )

    return verifier, sign, calls


def test_auth0_config_is_explicit_and_rejects_unsafe_modes(monkeypatch, auth_config):
    assert auth_config.resource == "https://test.example.org/mcp"
    for issuer in (
        "http://example.com",
        "https://example.com/path",
        "https://u:p@example.com",
        "https://bad..example.com",
    ):
        monkeypatch.setenv("WFC_AUTH0_ISSUER", issuer)
        with pytest.raises(ValueError):
            load_auth0_config("test.example.org")
    monkeypatch.setenv("WFC_AUTH0_ISSUER", auth_config.issuer)
    monkeypatch.setenv("WFC_AUTH0_ALLOWED_SUB", "")
    with pytest.raises(ValueError, match="ALLOWED_SUB"):
        load_auth0_config("test.example.org")
    monkeypatch.setenv("WFC_AUTH0_ALLOWED_SUB", auth_config.allowed_sub)
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", "")
    with pytest.raises(ValueError, match="CLIENT_ID"):
        load_auth0_config("test.example.org")
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", auth_config.client_id)
    monkeypatch.setenv("WFC_POC_NO_AUTH", "1")
    with pytest.raises(ValueError, match="mutually exclusive"):
        load_auth0_config("test.example.org")


def test_valid_token_binds_subject_resource_scopes_and_caches_jwks(jwt_case):
    verifier, sign, fetches = jwt_case
    verified = asyncio.run(verifier.verify_token(sign()))
    assert verified.subject == "auth0|owner"
    assert verified.resource == "https://test.example.org/mcp"
    assert verified.client_id == "https://chatgpt.com/oauth/client.json"
    assert verified.scopes == ["wfc:workflow"]
    assert asyncio.run(verifier.verify_token(sign())) is not None
    assert len(fetches) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"iss": "https://attacker.test/"},
        {"aud": "https://other.example.org/mcp"},
        {"sub": "auth0|someone-else"},
        {"scope": "openid profile"},
        {"exp": 1},
        {"iat": int(time.time()) + 10000},
        {"azp": ""},
        {"azp": "other-oauth-client"},
    ],
)
def test_wrong_issuer_audience_subject_scope_expiry_or_client_denied(jwt_case, override):
    verifier, sign, _ = jwt_case
    assert asyncio.run(verifier.verify_token(sign(**override))) is None


def test_invalid_signatures_and_algorithms_denied(jwt_case):
    verifier, sign, _ = jwt_case
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(sign(), options={"verify_signature": False})
    wrong_key = jwt.encode(
        claims, other, algorithm="RS256", headers={"kid": "key-1"}
    )
    wrong_algorithm = jwt.encode(
        claims, "x" * 32, algorithm="HS256", headers={"kid": "key-1"}
    )
    for token in (wrong_key, wrong_algorithm, "not.a.jwt", "x" * 9000):
        assert asyncio.run(verifier.verify_token(token)) is None


def test_jwks_unavailable_fails_closed(jwt_case):
    verifier, sign, _ = jwt_case

    async def network_failure():
        raise OSError("simulated outage")

    verifier._fetch_jwks = network_failure
    assert asyncio.run(verifier.verify_token(sign())) is None


def test_missing_sdk_http_identity_cannot_call_workflow(monkeypatch, auth_config):
    from my_wfc.security import authorize_context
    monkeypatch.setenv("WFC_AUTH_MODE", "auth0")
    with pytest.raises(PermissionError, match="OAuth"):
        authorize_context(None)


def test_auth0_mode_refuses_partial_server_configuration(monkeypatch):
    from my_wfc.server import create_mcp_server
    monkeypatch.setenv("WFC_AUTH_MODE", "auth0")
    monkeypatch.delenv("WFC_AUTH0_ISSUER", raising=False)
    monkeypatch.delenv("WFC_AUTH0_ALLOWED_SUB", raising=False)
    monkeypatch.setenv("WFC_PUBLIC_HOST", "test.example.org")
    with pytest.raises(ValueError, match="ISSUER"):
        create_mcp_server()


def test_auth0_transport_advertises_protected_resource(monkeypatch):
    from my_wfc.server import create_mcp_server
    monkeypatch.setenv("WFC_AUTH_MODE", "auth0")
    monkeypatch.setenv("WFC_POC_NO_AUTH", "0")
    monkeypatch.setenv("WFC_AUTH0_ISSUER", "https://example.us.auth0.com/")
    monkeypatch.setenv("WFC_AUTH0_ALLOWED_SUB", "auth0|owner")
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", "https://chatgpt.com/oauth/client.json")
    monkeypatch.setenv("WFC_PUBLIC_HOST", "test.example.org")
    oauth_server = create_mcp_server()
    app = oauth_server.streamable_http_app()
    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/mcp" in paths
    assert "/.well-known/oauth-protected-resource/mcp" in paths


def test_oauth_diagnostics_disabled_by_default(jwt_case, monkeypatch, caplog):
    verifier, sign, _ = jwt_case
    monkeypatch.delenv("WFC_AUTH0_DIAGNOSTICS", raising=False)
    caplog.set_level("WARNING", logger="my_wfc.auth0_oauth")
    assert asyncio.run(verifier.verify_token(sign(sub="auth0|other"))) is None
    assert not [r for r in caplog.records if "auth0_oauth_validation" in r.message]


def test_oauth_diagnostics_report_only_fixed_codes(jwt_case, monkeypatch, caplog):
    verifier, sign, _ = jwt_case
    monkeypatch.setenv("WFC_AUTH0_DIAGNOSTICS", "1")
    caplog.set_level("WARNING", logger="my_wfc.auth0_oauth")
    wrong_sub = sign(sub="auth0|someone-private")
    wrong_aud = sign(aud="https://internal.private.example/mcp")
    wrong_client = sign(azp="private-client-id")
    for token in (wrong_sub, wrong_aud, wrong_client):
        assert asyncio.run(verifier.verify_token(token)) is None
    assert asyncio.run(verifier.verify_token(sign())) is not None

    event_text = "\n".join(r.message for r in caplog.records
                           if "auth0_oauth_validation" in r.message)
    assert "code=subject_mismatch" in event_text
    assert "code=jwt_audience_mismatch" in event_text
    assert "code=client_mismatch" in event_text
    assert "code=verified" in event_text
    for sensitive in (wrong_sub, wrong_aud, wrong_client,
                      "auth0|someone-private", "private-client-id",
                      "https://internal.private.example/mcp"):
        assert sensitive not in event_text


def test_oauth_diagnostics_bounded(jwt_case, monkeypatch, caplog):
    verifier, sign, _ = jwt_case
    monkeypatch.setenv("WFC_AUTH0_DIAGNOSTICS", "1")
    caplog.set_level("WARNING", logger="my_wfc.auth0_oauth")
    invalid = sign(sub="auth0|other")
    for _ in range(70):
        assert asyncio.run(verifier.verify_token(invalid)) is None
    assert len([r for r in caplog.records
                if "auth0_oauth_validation" in r.message]) == 40


@pytest.mark.parametrize(
    "client_id",
    [
        "https://chatgpt.com/oauth/client.json",
        "https://chatgpt.com/oauth/BpZIpxL-aggt/client.json",
        "tpc_2cEspQ3q4E2X6saM9oKUE4",
    ],
)
def test_exact_cimd_identity_configuration_allowed(monkeypatch, auth_config, client_id):
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", client_id)
    assert load_auth0_config("test.example.org").client_id == client_id


@pytest.mark.parametrize(
    "client_id",
    [
        "https://evil.example/oauth/client.json",
        "http://chatgpt.com/oauth/client.json",
        "https://chatgpt.com/oauth/client.json?extra=1",
        "https://chatgpt.com/oauth/not-client.json",
        "https://chatgpt.com/oauth/client.json/extra",
        "chatgpt-client",
        "",
    ],
)
def test_oauth_client_identity_rejects_non_chatgpt_urls(
    monkeypatch, auth_config, client_id
):
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", client_id)
    with pytest.raises(ValueError, match="CLIENT_ID"):
        load_auth0_config("test.example.org")


def test_client_mismatch_distinguishes_expected_cimd_url_safely(
    jwt_case, monkeypatch, caplog
):
    verifier, sign, _ = jwt_case
    # Simulate operator configuring Auth0's internal tpc_ record instead of
    # the exact external CIMD URL used by ChatGPT in the OAuth flow.
    verifier.config = type(verifier.config)(
        issuer=verifier.config.issuer,
        resource=verifier.config.resource,
        allowed_sub=verifier.config.allowed_sub,
        client_id="tpc_2cEspQ3q4E2X6saM9oKUE4",
        scope=verifier.config.scope,
    )
    monkeypatch.setenv("WFC_AUTH0_DIAGNOSTICS", "1")
    caplog.set_level("WARNING", logger="my_wfc.auth0_oauth")
    assert asyncio.run(verifier.verify_token(sign())) is None
    messages = [r.message for r in caplog.records]
    assert any("code=client_mismatch_cimd_url" in msg for msg in messages)
    assert all("https://chatgpt.com/oauth/client.json" not in msg for msg in messages)


def test_expected_client_id_new_key_takes_effect(monkeypatch, auth_config):
    # The preferred key has the same strict validated identity semantics.
    monkeypatch.delenv("WFC_AUTH0_CLIENT_ID", raising=False)
    monkeypatch.setenv(
        "WFC_OAUTH_EXPECTED_CLIENT_ID", "https://chatgpt.com/oauth/client.json"
    )
    assert load_auth0_config("test.example.org").client_id == auth_config.client_id


def test_legacy_client_id_env_remains_compatible(monkeypatch, auth_config):
    monkeypatch.delenv("WFC_OAUTH_EXPECTED_CLIENT_ID", raising=False)
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", auth_config.client_id)
    assert load_auth0_config("test.example.org").client_id == auth_config.client_id


def test_client_id_env_conflicts_fail_closed(monkeypatch, auth_config):
    monkeypatch.setenv(
        "WFC_OAUTH_EXPECTED_CLIENT_ID", "https://chatgpt.com/oauth/client.json"
    )
    monkeypatch.setenv(
        "WFC_AUTH0_CLIENT_ID", "tpc_2cEspQ3q4E2X6saM9oKUE4"
    )
    with pytest.raises(ValueError, match="conflicting"):
        load_auth0_config("test.example.org")


def test_both_client_id_keys_equal_allowed(monkeypatch, auth_config):
    monkeypatch.setenv("WFC_AUTH0_CLIENT_ID", auth_config.client_id)
    monkeypatch.setenv("WFC_OAUTH_EXPECTED_CLIENT_ID", auth_config.client_id)
    assert load_auth0_config("test.example.org").client_id == auth_config.client_id
