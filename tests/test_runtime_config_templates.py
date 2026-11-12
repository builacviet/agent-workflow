"""Contract for operator-facing runtime examples (no real secret reads)."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"


def test_v1_controller_stack_auth0_template_is_single_source() -> None:
    template = (RUNTIME / "controller-mcp.env.example").read_text(encoding="utf-8")
    assert "WFC_AUTH_MODE=auth0" in template
    assert "WFC_POC_NO_AUTH=0" in template
    assert "WFC_AUTH0_ISSUER=" in template
    assert "WFC_AUTH0_ALLOWED_SUB=" in template
    assert "WFC_OAUTH_EXPECTED_CLIENT_ID=" in template
    assert not (RUNTIME / "mcp-auth0.env.example").exists()


def test_active_runtime_templates_have_documented_owners() -> None:
    inventory = (RUNTIME / "README.md").read_text(encoding="utf-8")
    for path in (
        "controller-ci.json.example",
        "controller-mcp.env.example",
        "controller-webhook.env.example",
        "controller.json.example",
        "ngrok.env.example",
    ):
        assert (RUNTIME / path).is_file()
        assert path in inventory
    assert "WFC_" in inventory
    assert not (RUNTIME / "mcp.env.example").exists()
    compose = ROOT / "compose.yaml"
    assert compose.exists()
    body = compose.read_text(encoding="utf-8")
    assert "name: my-wfc-phase31" in body
    for service in ("mcp:", "webhook:", "router:", "ngrok:"):
        assert service in body
    assert not (ROOT / "compose.controller.yaml").exists()

def test_nginx_dynamic_service_dns_preserves_routes() -> None:
    config = (ROOT / "nginx/controller-router.conf").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11" in config
    assert "proxy_pass http://$webhook_upstream;" in config
    assert "proxy_pass http://$mcp_upstream;" in config
    assert "proxy_pass http://webhook:8081;" not in config
    assert "proxy_pass http://mcp:8000;" not in config
    assert "location = /webhooks/github" in config
    assert "location / {" in config


def test_sandbox_state_config_rejects_unsafe_paths(tmp_path):
    import pytest
    from agent_workflow.app_config import AppConfig

    assert AppConfig.from_env({}).sandbox_state_dir == Path.home() / ".agent-workflow" / "sandbox-fences"
    assert AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(tmp_path)}).sandbox_state_dir == tmp_path
    for value in ("", "relative/path", str(tmp_path / ".." / "escape"),
                  "postgresql://user:secret-password@host/db", "bad\x00path"):
        with pytest.raises(ValueError) as error:
            AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": value})
        assert "secret-password" not in str(error.value)
