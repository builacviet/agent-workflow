import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pg_preflight", ROOT / "scripts/pg_dev_preflight.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def config(tmp_path, content):
    path = tmp_path / "dev.env"
    path.write_text(content, encoding="utf-8")
    return module.load_config(path)


BASE = "PG_DEV_DB=db\nPG_DEV_USER=user\nPG_DEV_PASSWORD= pass $literal # \n"


def test_literal_password_and_default_port(tmp_path):
    values = config(tmp_path, BASE)
    assert values["PG_DEV_PASSWORD"] == " pass $literal # "
    assert values["PG_DEV_PORT"] == "55432"


@pytest.mark.parametrize("key", ["PG_DEV_DB", "PG_DEV_USER", "PG_DEV_PASSWORD"])
@pytest.mark.parametrize("value", ["", "   "])
def test_required_values(tmp_path, key, value):
    lines = {"PG_DEV_DB": "db", "PG_DEV_USER": "user", "PG_DEV_PASSWORD": "secret"}
    lines[key] = value
    with pytest.raises(ValueError):
        config(tmp_path, "\n".join(f"{k}={v}" for k, v in lines.items()))
    del lines[key]
    with pytest.raises(ValueError):
        config(tmp_path, "\n".join(f"{k}={v}" for k, v in lines.items()))


@pytest.mark.parametrize("port", ["", "0", "65536", "-1", "1.5", "１２", " 5432"])
def test_bad_port(tmp_path, port):
    with pytest.raises(ValueError):
        config(tmp_path, BASE + f"PG_DEV_PORT={port}\n")


@pytest.mark.parametrize("port", ["1", "65535"])
def test_valid_port(tmp_path, port):
    assert config(tmp_path, BASE + f"PG_DEV_PORT={port}\n")["PG_DEV_PORT"] == port


def test_duplicate_and_unknown_keys_fail(tmp_path):
    for extra in ("PG_DEV_DB=other\n", "OTHER=value\n", "invalid\n"):
        with pytest.raises(ValueError):
            config(tmp_path, BASE + extra)


def test_invalid_config_never_invokes_docker(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config"
    path.write_text("PG_DEV_PASSWORD=secret\n", encoding="utf-8")
    monkeypatch.setattr(module, "ENV_FILE", path)
    monkeypatch.setattr(module.sys, "argv", ["preflight", "down"])
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("Docker invoked"))
    assert module.main() == 2
    assert "secret" not in capsys.readouterr().err


def compose_test_environment():
    env = os.environ.copy()
    env.update(
        PG_DEV_DB="test_db", PG_DEV_USER="test_user",
        PG_DEV_PASSWORD="test_password", PG_DEV_PORT="55432",
    )
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"):
        env.pop(key, None)
    return env


@pytest.mark.parametrize("args", [
    [], ["--project-name", "other", "down"], ["-f", "other.yml", "down"],
    ["--env-file", "other.env", "down"], ["down", "--volumes"],
    ["down", "-v"], ["down", "--project-name", "other"],
    ["run", "postgres", "sh"], ["up", "--wait-timeout", "999"],
])
def test_unapproved_commands_never_invoke_docker(args, monkeypatch):
    monkeypatch.setattr(module.sys, "argv", ["preflight", *args])
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("Docker invoked"))
    assert module.main() == 2


def test_approved_command_uses_validated_environment(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.write_text(BASE, encoding="utf-8")
    monkeypatch.setattr(module, "ENV_FILE", path)
    monkeypatch.setattr(module.sys, "argv", ["preflight", "up", "-d", "--wait", "--wait-timeout", "120"])
    monkeypatch.setenv("PG_DEV_PASSWORD", "ambient")
    monkeypatch.setenv("COMPOSE_FILE", "other.yml")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main() == 0
    command, options = calls[0]
    assert command == ["docker", "compose", "--project-name", "agent-workflow-pg-dev",
                       "--file", str(ROOT / "compose.postgres.dev.yaml"),
                       "up", "-d", "--wait", "--wait-timeout", "120"]
    assert options["env"]["PG_DEV_PASSWORD"] == " pass $literal # "
    assert "COMPOSE_FILE" not in options["env"]
    assert options["timeout"] == 180


def test_timeout_is_reported_without_secrets(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config"
    path.write_text(BASE, encoding="utf-8")
    monkeypatch.setattr(module, "ENV_FILE", path)
    monkeypatch.setattr(module.sys, "argv", ["preflight", "down"])
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret", 180)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    assert module.main() == 1
    assert "secret" not in capsys.readouterr().err


def test_compose_boundaries():
    text = (ROOT / "compose.postgres.dev.yaml").read_text(encoding="utf-8-sig")
    for expected in ("postgres:16", "127.0.0.1:${PG_DEV_PORT:-55432}:5432", "unless-stopped", "pg_isready", "interval: 5s", "timeout: 3s", "retries: 12", "start_period: 20s", "pg_data:", "driver: bridge"):
        assert expected in text
    for forbidden in ("privileged:", "network_mode:", "external:", "docker.sock", "cap_add:"):
        assert forbidden not in text


def test_effective_compose_config():
    if not shutil.which("docker"):
        pytest.skip("Docker CLI unavailable")
    env = compose_test_environment()
    result = subprocess.run(
        ["docker", "compose", "-p", "pgdevconfigtest", "-f",
         str(ROOT / "compose.postgres.dev.yaml"), "config", "--format", "json"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, "Compose config failed"
    config = json.loads(result.stdout)
    service = config["services"]["postgres"]
    assert service["image"] == "postgres:16"
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == "55432"
    assert not service.get("privileged") and not service.get("network_mode") and not service.get("cap_add")
    assert all(not network.get("external") for network in config["networks"].values())
    assert all("docker.sock" not in str(volume) for volume in service["volumes"])


def test_disposable_compose_lifecycle():
    """Opt-in integration test; all resources use a random test-only project."""
    if os.environ.get("PG_DEV_LIVE_TEST") != "1":
        pytest.skip("Set PG_DEV_LIVE_TEST=1 on a disposable Docker host")
    project = "pgdevtest" + uuid.uuid4().hex[:12]
    env = compose_test_environment()
    base = ["docker", "compose", "-p", project, "-f", str(ROOT / "compose.postgres.dev.yaml")]

    def run(*args):
        return subprocess.run(
            [*base, *args], env=env, cwd=ROOT, capture_output=True,
            text=True, timeout=180, check=True,
        )

    def sql(statement):
        return run(
            "exec", "-T", "postgres", "psql", "-U", "test_user",
            "-d", "test_db", "-Atc", statement,
        ).stdout.strip()

    try:
        run("up", "-d", "--wait", "--wait-timeout", "120")
        sql("CREATE TABLE smoke (value integer); INSERT INTO smoke VALUES (42)")
        run("stop")
        run("start")
        run("up", "-d", "--wait", "--wait-timeout", "120")
        assert sql("SELECT value FROM smoke") == "42"
        run("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        assert sql("SELECT value FROM smoke") == "42"
        run("down")
        run("up", "-d", "--wait", "--wait-timeout", "120")
        assert sql("SELECT value FROM smoke") == "42"
        container = run("ps", "-q", "postgres").stdout.strip()
        address = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
        assert address
        probe = ["--entrypoint", "pg_isready", "postgres:16", "-h", address,
                 "-p", "5432", "-U", "test_user", "-d", "test_db", "-t", "5"]
        connected = subprocess.run(
            ["docker", "run", "--rm", "--network", f"{project}_pg_dev", *probe],
            capture_output=True, text=True, timeout=30,
        )
        assert connected.returncode == 0, "Networked positive control failed"
        denied = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", *probe],
            capture_output=True, text=True, timeout=30,
        )
        assert denied.returncode == 2, "Expected pg_isready no-response status"

    finally:
        run("down", "--volumes", "--remove-orphans")
