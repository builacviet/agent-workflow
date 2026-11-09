"""Device auth integration without reading real tokens or calling a model."""

import hashlib
from pathlib import Path
import runpy
import sys
import time
from types import SimpleNamespace

import pytest
from agent_workflow.runners.coder_runtime import CoderRuntime


@pytest.mark.parametrize(
    "mode", ["code", "test", "prepare", "publish", "probe"]
)
def test_only_code_gets_device_auth_mount(tmp_path, monkeypatch, mode):
    import agent_workflow.runners.coder_runtime as module

    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    pem = tmp_path / "app.pem"
    pem.write_text("synthetic")
    config = {
        "image": "fixture",
        "model": "fixture-model",
        "pem": str(pem),
        "codex_auth_volume": "techlead-review-poc_codex_auth",
        "seccomp_profile": str(profile),
        "seccomp_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
    }
    calls, commands = [], []
    monkeypatch.setattr(
        module, "docker_command", lambda args: commands.append(args)
    )
    monkeypatch.setattr(
        module,
        "run_container",
        lambda args, *rest: calls.append(args) or b"ok",
    )
    runtime = CoderRuntime(config, None)
    claim = {
        "dispatch_id": "a" * 32,
        "input_sha": "b" * 40,
        "deadline": time.time() + 60,
    }
    code_plan = (
        runtime.registry.plan(runtime._code_request(claim)) if mode == "code" else None
    )
    runtime.docker(claim, tmp_path, mode, code_plan=code_plan)
    text = " ".join(calls[0])
    assert ("src=techlead-review-poc_codex_auth" in text) == (mode == "code")
    assert bool(commands) == (mode == "code")
    assert "openai_api_key" not in text
    if mode == "test":
        assert "--network=none" in text


@pytest.mark.parametrize("logged_in", [True, False])
def test_entry_uses_file_login_without_api_key(
    tmp_path, monkeypatch, logged_in
):
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    auth = tmp_path / "auth"
    auth.mkdir()
    if logged_in:
        (auth / "auth.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(auth))
    monkeypatch.setenv("WFC_CODEX_MODEL", "fixture")
    monkeypatch.setenv("CODEX_API_KEY", "synthetic-disallowed")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-disallowed")
    monkeypatch.setattr(sys, "argv", ["entry", "code"])
    calls = []

    def run(args, **kwargs):
        import os

        assert (
            "CODEX_API_KEY" not in os.environ
            and "OPENAI_API_KEY" not in os.environ
        )
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "docker/coder_entry.py"),
            run_name="__main__",
        )
    if logged_in:
        assert result.value.code == 0
        assert 'cli_auth_credentials_store="file"' in calls[0]
    else:
        assert "Device login missing" in str(result.value)
        assert not calls
