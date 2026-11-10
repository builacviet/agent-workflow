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


def test_active_and_legacy_runtime_templates_have_documented_owners() -> None:
    inventory = (RUNTIME / "README.md").read_text(encoding="utf-8")
    for path in (
        "controller-ci.json.example",
        "controller-mcp.env.example",
        "controller-webhook.env.example",
        "controller.json.example",
        "mcp.env.example",
        "ngrok.env.example",
    ):
        assert (RUNTIME / path).is_file()
        assert path in inventory
    assert "WFC_" in inventory
