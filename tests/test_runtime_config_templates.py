"""Contract for operator-facing runtime examples (no real secret reads)."""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

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

    assert (
        AppConfig.from_env({}).sandbox_state_dir
        == Path.home() / ".agent-workflow" / "sandbox-fences"
    )
    assert (
        AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(tmp_path)}).sandbox_state_dir
        == tmp_path
    )
    for value in (
        "",
        "relative/path",
        str(tmp_path / ".." / "escape"),
        "postgresql://user:secret-password@host/db",
        "bad\x00path",
    ):
        with pytest.raises(ValueError) as error:
            AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": value})
        assert "secret-password" not in str(error.value)


def test_sandbox_state_rejects_windows_remote_and_device_paths():
    import pytest

    from agent_workflow.app_config import AppConfig

    for path in (
        r"\\server\share\fences",
        r"\\?\C:\private\fences",
        r"\\.\C:\private\fences",
        "//server/share/fences",
    ):
        with pytest.raises(ValueError, match="safe, durable local directory"):
            AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": path})


def test_sandbox_state_rejects_redirecting_components(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    import pytest

    from agent_workflow.app_config import AppConfig

    parent = tmp_path / "redirect"
    parent.mkdir()
    target = parent / "fences"
    original = Path.lstat
    for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)):

        def fake_lstat(path, *args, **kwargs):
            if path == parent:
                return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
            return original(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "lstat", fake_lstat)
            with pytest.raises(ValueError, match="safe, durable local directory"):
                AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(target)})


def test_sandbox_default_resolves_home_at_instantiation(tmp_path, monkeypatch):
    from agent_workflow.app_config import AppConfig

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert (
        AppConfig.from_env({}).sandbox_state_dir
        == tmp_path / ".agent-workflow" / "sandbox-fences"
    )


def test_sandbox_state_rejects_remote_mapped_drive(tmp_path, monkeypatch):
    import os

    import pytest

    from agent_workflow import sandbox_state_paths
    from agent_workflow.app_config import AppConfig

    if os.name != "nt":
        pytest.skip("Windows drive classification")
    monkeypatch.setattr(
        sandbox_state_paths, "_windows_fixed_drive", lambda anchor: False
    )
    with pytest.raises(ValueError, match="safe, durable local directory"):
        AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(tmp_path)})


def test_mount_classifier_uses_longest_prefix_and_decodes_spaces():
    from agent_workflow.sandbox_state_paths import _linux_mount_type

    mounts = (
        "25 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
        "26 25 0:77 / /mnt/nfs rw - nfs4 server:/nfs rw\n"
        "27 25 8:2 / /mnt/local rw - xfs /dev/sdb rw\n"
        "28 25 0:89 / /mnt/team\\040share rw - cifs //srv/share rw\n"
    )
    assert _linux_mount_type(Path("/mnt/nfs/fences"), mounts) == "nfs4"
    assert _linux_mount_type(Path("/mnt/local/fences"), mounts) == "xfs"
    assert _linux_mount_type(Path("/mnt/local2/fences"), mounts) == "ext4"
    assert _linux_mount_type(Path("/mnt/team share/fences"), mounts) == "cifs"


def test_linux_network_volatile_and_unknown_mounts_fail_closed(tmp_path, monkeypatch):
    import sys

    import pytest

    from agent_workflow import sandbox_state_paths
    from agent_workflow.app_config import AppConfig

    if sys.platform != "linux":
        pytest.skip("Linux-specific mount table")
    for fs_type in ("nfs4", "cifs", "fuse.sshfs", "9p", "tmpfs", "unknown"):
        with monkeypatch.context() as patch:
            patch.setattr(
                sandbox_state_paths, "_linux_mount_type", lambda *args: fs_type
            )
            with pytest.raises(ValueError, match="safe, durable local directory"):
                AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(tmp_path / "fences")})


def test_sandbox_state_rejects_world_writable_leaf(tmp_path):
    import os

    import pytest

    from agent_workflow.app_config import AppConfig

    if os.name == "nt":
        pytest.skip("POSIX mode check; Windows ACLs are operator-controlled")
    directory = tmp_path / "untrusted"
    directory.mkdir()
    directory.chmod(0o777)
    with pytest.raises(ValueError, match="safe, durable local directory"):
        AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(directory)})


def test_sandbox_state_rejects_filesystem_root_and_unreadable_mount(
    tmp_path, monkeypatch
):
    import sys

    import pytest

    from agent_workflow import sandbox_state_paths
    from agent_workflow.app_config import AppConfig

    with pytest.raises(ValueError, match="safe, durable local directory"):
        AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(Path(tmp_path.anchor))})
    if sys.platform == "linux":

        def unavailable(*args):
            raise OSError("synthetic mount outage")

        with monkeypatch.context() as patch:
            patch.setattr(sandbox_state_paths, "_linux_mount_type", unavailable)
            with pytest.raises(ValueError, match="safe, durable local directory"):
                AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(tmp_path / "state")})


def test_techlead_template_defaults() -> None:
    text = (RUNTIME / "controller-mcp.env.example").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if line and not line.startswith("#")
    )
    assert {key for key in values if key.startswith("TECHLEAD_")} == {
        "TECHLEAD_GITHUB_APP_ID",
        "TECHLEAD_GITHUB_INSTALLATION_ID",
        "TECHLEAD_GITHUB_APP_SLUG",
        "TECHLEAD_GITHUB_BOT_ID",
        "TECHLEAD_GITHUB_KEY_FILE",
        "TECHLEAD_GITHUB_TEST_PRS",
        "TECHLEAD_NOTION_BOT_ID",
        "TECHLEAD_NOTION_TOKEN_FILE",
        "TECHLEAD_NOTION_TASK_PAGES",
        "TECHLEAD_NOTION_TEST_PAGES",
    }
    assert values["TECHLEAD_GITHUB_KEY_FILE"] == "/run/secrets/wf-teamlead.pem"
    assert values["TECHLEAD_NOTION_TOKEN_FILE"] == "/run/secrets/notion-tl.token"
    for key, value in values.items():
        if key.startswith("TECHLEAD_") and not key.endswith("_FILE"):
            assert value == ""
    assert values["WFC_AUTH0_SCOPE"] == "wfc:workflow"
    dockerfile = (ROOT / "docker/mcp.Dockerfile").read_text(encoding="utf-8")
    assert "--uid 10001" in dockerfile
    assert "USER appuser" in dockerfile
    assert not (ROOT / "compose.techlead.yaml").exists()


@pytest.fixture
def compose_fixture(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI unavailable")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COMPOSE_", "TECHLEAD_"))
    }
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    for name in ("controller-mcp.env", "controller-webhook.env", "ngrok.env"):
        (runtime / name).write_text("", encoding="utf-8")
    source = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    path = tmp_path / "compose.yaml"
    path.write_text(source, encoding="utf-8")
    command = [
        "docker",
        "compose",
        "--env-file",
        str(tmp_path / "empty.env"),
        "--profile",
        "*",
        "-p",
        "tl-fixture-" + uuid.uuid4().hex[:12],
        "-f",
        str(path),
    ]
    return path, source, env, command


def compose_config(fixture):
    path, source, env, command = fixture
    return subprocess.run(
        command + ["config", "--format", "json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def activate_mounts(path, source):
    lines = source.splitlines(keepends=True)
    declarations = [
        line for line in lines if line.startswith(("      # -", "      #   "))
    ]
    assert len(declarations) == 12
    path.write_text(
        "".join(
            line.replace("      # ", "      ", 1) if line in declarations else line
            for line in lines
        ),
        encoding="utf-8",
    )


def test_default_and_activated_compose(compose_fixture, tmp_path):
    path, source, env, command = compose_fixture
    default = compose_config(compose_fixture)
    assert default.returncode == 0, default.stderr
    baseline = json.loads(default.stdout)
    services = baseline["services"]
    assert set(services) == {"mcp", "webhook", "router", "ngrok"}
    assert "command" not in services["mcp"] or services["mcp"]["command"] is None
    assert "entrypoint" not in services["mcp"] or services["mcp"]["entrypoint"] is None
    assert len(services["mcp"]["volumes"]) == 1
    assert services["mcp"]["volumes"][0]["target"] == "/state"
    for name in ("mcp", "webhook"):
        assert services[name]["read_only"] is True
        assert services[name]["cap_drop"] == ["ALL"]
        assert services[name]["security_opt"] == ["no-new-privileges:true"]
    activate_mounts(path, source)
    for value in (None, ""):
        if value is None:
            env.pop("TECHLEAD_SECRETS_DIR", None)
        else:
            env["TECHLEAD_SECRETS_DIR"] = value
        missing = compose_config(compose_fixture)
        assert missing.returncode != 0
        assert "Set TECHLEAD_SECRETS_DIR" in missing.stderr
    secret_dir = tmp_path / "disposable-secrets"
    secret_dir.mkdir()
    for name in ("wf-teamlead.pem", "notion-tl.token"):
        (secret_dir / name).write_text("placeholder", encoding="utf-8")
    env["TECHLEAD_SECRETS_DIR"] = secret_dir.as_posix()
    enabled = compose_config(compose_fixture)
    assert enabled.returncode == 0, enabled.stderr
    actual = json.loads(enabled.stdout)
    mounts = actual["services"]["mcp"]["volumes"]
    for name in ("wf-teamlead.pem", "notion-tl.token"):
        mount = next(m for m in mounts if m["target"] == "/run/secrets/" + name)
        assert mount["type"] == "bind"
        assert Path(mount["source"]) == secret_dir / name
        assert mount["read_only"] is True
        assert mount["bind"]["create_host_path"] is False
    actual["services"]["mcp"]["volumes"] = [
        m for m in mounts if m["target"] == "/state"
    ]
    assert (
        actual == baseline
    )  # All other service, network and security fields unchanged.


@pytest.mark.skipif(
    os.environ.get("WFC_TEST_TL_MOUNTS") != "1",
    reason="Opt-in disposable Docker mount/start checks",
)
def test_activated_mount_existing_files(compose_fixture, tmp_path):
    path, source, env, command = compose_fixture
    activate_mounts(path, source)
    secret_dir = tmp_path / "disposable-secrets"
    secret_dir.mkdir()
    names = ("wf-teamlead.pem", "notion-tl.token")
    for name in names:
        (secret_dir / name).write_text("placeholder", encoding="utf-8")
    env["TECHLEAD_SECRETS_DIR"] = secret_dir.as_posix()
    result = compose_config(compose_fixture)
    assert result.returncode == 0, result.stderr
    mounts = [
        m
        for m in json.loads(result.stdout)["services"]["mcp"]["volumes"]
        if m["target"].startswith("/run/secrets/")
    ]
    # Use only parsed mounts with an existing local image, no build/pull/network,
    # no real service env, ports, state or provider calls.
    check = (
        "import os, pathlib; assert os.getuid() == 10001; "
        "paths=[pathlib.Path('/run/secrets/wf-teamlead.pem'),"
        "pathlib.Path('/run/secrets/notion-tl.token')]; "
        "assert all(p.is_file() for p in paths); "
        "[p.open('rb').close() for p in paths]"
    )
    fixture = {
        "services": {
            "mcp": {
                "image": "python:3.12-alpine",
                "user": "10001:10001",
                "network_mode": "none",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "volumes": mounts,
                "entrypoint": ["python", "-c", check],
            }
        }
    }
    path.write_text(json.dumps(fixture), encoding="utf-8")
    run = command + ["run", "--rm", "--no-deps", "--pull", "never", "mcp"]
    result = subprocess.run(run, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "oversized"])
def test_existing_secret_reader_rejects_invalid_fixture(tmp_path, kind):
    from agent_workflow.mcp.techlead_github import TechLeadError, read_secret

    path = tmp_path / "fixture-secret"
    if kind == "directory":
        path.mkdir()
    elif kind == "empty":
        path.write_bytes(b"")
    elif kind == "oversized":
        path.write_bytes(b"x" * 17)
    with pytest.raises(TechLeadError, match="Tech Lead secret file unavailable"):
        read_secret(path, 16)


def test_existing_secret_reader_accepts_placeholder(tmp_path):
    from agent_workflow.mcp.techlead_github import read_secret

    path = tmp_path / "fixture-secret"
    path.write_bytes(b"placeholder\n")
    assert read_secret(path, 16) == b"placeholder"
