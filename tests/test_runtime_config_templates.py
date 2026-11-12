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
    assert AppConfig.from_env({}).sandbox_state_dir == tmp_path / ".agent-workflow" / "sandbox-fences"


def test_sandbox_state_rejects_remote_mapped_drive(tmp_path, monkeypatch):
    import os
    import pytest
    from agent_workflow import sandbox_state_paths
    from agent_workflow.app_config import AppConfig

    if os.name != "nt":
        pytest.skip("Windows drive classification")
    monkeypatch.setattr(sandbox_state_paths, "_windows_fixed_drive", lambda anchor: False)
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
    from agent_workflow.app_config import AppConfig
    from agent_workflow import sandbox_state_paths

    if sys.platform != "linux":
        pytest.skip("Linux-specific mount table")
    for fs_type in ("nfs4", "cifs", "fuse.sshfs", "9p", "tmpfs", "unknown"):
        with monkeypatch.context() as patch:
            patch.setattr(sandbox_state_paths, "_linux_mount_type", lambda *args: fs_type)
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


def test_sandbox_state_rejects_filesystem_root_and_unreadable_mount(tmp_path, monkeypatch):
    import sys
    import pytest
    from agent_workflow.app_config import AppConfig
    from agent_workflow import sandbox_state_paths

    with pytest.raises(ValueError, match="safe, durable local directory"):
        AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(Path(tmp_path.anchor))})
    if sys.platform == "linux":
        def unavailable(*args):
            raise OSError("synthetic mount outage")
        with monkeypatch.context() as patch:
            patch.setattr(sandbox_state_paths, "_linux_mount_type", unavailable)
            with pytest.raises(ValueError, match="safe, durable local directory"):
                AppConfig.from_env({"WFC_SANDBOX_STATE_DIR": str(tmp_path / "state")})
