"""Offline sandbox contract tests and opt-in Docker conformance fixtures.

Run the real cases with WFC_SANDBOX_TEST_IMAGE set to a locally cached trusted
Linux Python 3 image with no ENTRYPOINT or VOLUME. No image is built or pulled.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import pytest

from agent_workflow.integrations.github import WorkflowError
from agent_workflow.runners import coder_runtime, sandbox
from agent_workflow.runners.sandbox import SandboxManager, SandboxPolicy


@pytest.mark.parametrize(
    "values",
    [
        {"version": True},
        {"version": 2},
        {"workspace_writable": 1},
        {"network": "bridge"},
        {"pids_limit": 0},
        {"memory_mib": 2049},
        {"cpus": True},
        {"timeout_seconds": 3601},
    ],
)
def test_invalid_policy_is_rejected(values):
    with pytest.raises(ValueError):
        SandboxPolicy(**values)


def test_unavailable_engine_enforcement_prevents_launch(monkeypatch, tmp_path):
    calls = []

    def docker(args):
        calls.append(args)
        return b'"linux" true false true true ["name=seccomp,profile=builtin"]'

    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="cannot enforce"):
        SandboxManager().run(
            SandboxPolicy(),
            dispatch_id="a" * 32,
            workspace=tmp_path,
            image="sandbox-fixture",
            command=("python",),
        )
    assert [args[0] for args in calls] == ["info"]


def test_declared_image_volumes_fail_closed(monkeypatch):
    def docker(args):
        return json.dumps([{
            "Id": "sha256:" + "b" * 64,
            "Os": "linux",
            "Config": {"Volumes": {"/data": {}}},
        }]).encode()

    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="without declared volumes"):
        sandbox._local_image("sandbox-fixture")


def test_workspace_hardlink_is_rejected(tmp_path):
    source = tmp_path / "outside-synthetic"
    source.write_text("fixture", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    os.link(source, workspace / "alias")
    with pytest.raises(WorkflowError, match="link or special file"):
        sandbox._workspace_bind(workspace, True)


def test_docker_argument_boundary_and_timeout_cleanup(monkeypatch, tmp_path):
    identifier = "b" * 64
    dispatch = "a" * 32
    calls = []
    owned = None

    def docker(args):
        nonlocal owned
        calls.append(args)
        match args[0]:
            case "info":
                return b'"linux" true true true true ["name=seccomp,profile=builtin"]'
            case "image":
                return json.dumps([{
                    "Id": "sha256:" + identifier,
                    "Os": "linux",
                    "Config": {},
                }]).encode()
            case "create":
                owned = args[args.index("--name") + 1]
                return identifier.encode()
            case "container":
                if owned:
                    return json.dumps({
                        "ID": identifier,
                        "Names": owned,
                        "Labels": f"org.agent-workflow.sandbox.dispatch={dispatch}",
                    }).encode()
                return b""
            case "rm":
                assert args == ["rm", "-f", identifier]
                owned = None
                return b""
        raise AssertionError(args)

    def attach(args, stdin, remaining):
        assert args == ["docker", "start", "-ai", identifier]
        assert stdin == "task input"
        assert 0 < remaining <= 10
        raise TimeoutError("fixture timeout")

    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(coder_runtime, "run_container", attach)
    with pytest.raises(TimeoutError, match="fixture timeout"):
        SandboxManager().run(
            SandboxPolicy(workspace_writable=False, timeout_seconds=10),
            dispatch_id=dispatch,
            workspace=tmp_path,
            image="sandbox-fixture",
            command=("code", "--privileged"),
            stdin="task input",
            env=(("MODEL", "--network=host"),),
        )

    assert [args[0] for args in calls] == [
        "info", "image", "create", "container", "rm", "container"
    ]
    create = calls[2]
    assert "--network=none" in create
    assert "--read-only" in create
    assert "--cap-drop=ALL" in create
    assert "--security-opt=no-new-privileges:true" in create
    assert "--memory=2048m" in create
    assert "--memory-swap=2048m" in create
    assert "--cpus=2" in create
    assert "--pids-limit=256" in create
    assert create[create.index("--mount") + 1].endswith(
        "bind-recursive=disabled,readonly"
    )
    assert "MODEL=--network=host" in create
    assert create[-3:] == ["sha256:" + identifier, "code", "--privileged"]
    assert owned is None


def test_cleanup_rejects_unowned_id(monkeypatch):
    calls = []

    def docker(args):
        calls.append(args)
        return json.dumps({
            "ID": "b" * 64,
            "Names": "unrelated",
            "Labels": "",
        }).encode()

    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="verify sandbox ownership"):
        SandboxManager().stop("a" * 32)
    assert len(calls) == 1


@pytest.fixture
def live_sandbox(tmp_path):
    image = os.environ.get("WFC_SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("set WFC_SANDBOX_TEST_IMAGE for disposable Docker conformance")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.chmod(0o777)
    dispatch = uuid4().hex
    manager = SandboxManager()

    def run(script, *args, policy=None):
        return manager.run(
            policy or SandboxPolicy(timeout_seconds=20),
            dispatch_id=dispatch,
            workspace=workspace,
            image=image,
            command=("python", "-c", script, *args),
            phase="test",
        )

    return run, manager, dispatch, workspace, image


def test_s01_allowed_workspace_read(live_sandbox):
    run, _, _, workspace, _ = live_sandbox
    (workspace / "read.txt").write_text("workspace fixture", encoding="utf-8")
    assert b"workspace fixture" in run("print(open('read.txt').read())")


def test_s02_allowed_workspace_write(live_sandbox):
    run, _, _, workspace, _ = live_sandbox
    run("from pathlib import Path; Path('write.txt').write_text('changed')")
    assert (workspace / "write.txt").read_text(encoding="utf-8") == "changed"


def test_s03_legitimate_shell_and_test_execution(live_sandbox):
    run, *_ = live_sandbox
    output = run(
        "import subprocess; "
        "subprocess.run(['sh', '-c', 'test -d /workspace && printf shell-ok'], "
        "check=True)"
    )
    assert b"shell-ok" in output


def test_s04_denied_filesystem_write(live_sandbox):
    run, _, _, workspace, _ = live_sandbox
    run(
        """\
from pathlib import Path

for path in (Path('/etc/sandbox-write'), Path('/workspace/forbidden')):
    try:
        path.write_text('forbidden')
    except OSError as error:
        assert error.errno in {1, 13, 30}, error
    else:
        raise AssertionError('write succeeded')
""",
        policy=SandboxPolicy(workspace_writable=False, timeout_seconds=20),
    )
    assert not (workspace / "forbidden").exists()


def test_s05_denied_synthetic_host_secret_read(live_sandbox):
    run, _, _, workspace, _ = live_sandbox
    secret = workspace.parent / f"protected-{uuid4().hex}"
    secret.write_text("synthetic credential", encoding="utf-8")
    assert secret.is_file()
    run(
        """\
from pathlib import Path
import sys

for path in sys.argv[1:]:
    try:
        Path(path).read_bytes()
    except OSError as error:
        assert error.errno in {1, 2, 13}, error
    else:
        raise AssertionError('host secret was readable')
""",
        str(secret),
        f"/workspace/../{secret.name}",
    )


def test_s06_unauthorized_network_denied(live_sandbox):
    run, *_ = live_sandbox
    run(
        """\
import errno
import socket

assert {name for _, name in socket.if_nameindex()} == {'lo'}
with socket.socket() as client:
    client.settimeout(2)
    assert client.connect_ex(('192.0.2.1', 443)) in {errno.ENETUNREACH, errno.EACCES}
"""
    )


def test_s07_privilege_escalation_denied(live_sandbox):
    run, *_ = live_sandbox
    run(
        """\
import os
from pathlib import Path

status = dict(line.split(':', 1) for line in
              Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert os.geteuid() == 10001
assert int(status['CapEff'], 16) == 0
assert int(status['NoNewPrivs']) == 1
assert int(status['Seccomp']) == 2
try:
    os.setuid(0)
except PermissionError:
    pass
else:
    raise AssertionError('escalation succeeded')
"""
    )


def test_s08_timeout_and_cleanup_ownership(live_sandbox):
    run, manager, dispatch, workspace, image = live_sandbox
    name = f"my-wfc-sandbox-{dispatch}-test-{uuid4().hex}"
    fixture = subprocess.run(
        [
            "docker", "create", "--pull=never", "--network=none",
            "--name", name, image, "true",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", fixture)
    try:
        with pytest.raises((TimeoutError, WorkflowError), match="deadline"):
            run(
                "from pathlib import Path; import time; "
                "Path('started').touch(); time.sleep(30)",
                policy=SandboxPolicy(timeout_seconds=6),
            )
        assert (workspace / "started").is_file(), "test workload never started"
        ids = subprocess.run(
            [
                "docker", "container", "ls", "-aq", "--no-trunc", "--filter",
                f"name=^/my-wfc-sandbox-{dispatch}-",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.split()
        assert ids == [fixture], "timed-out invocation was not cleaned up"
        manager.stop(dispatch)
        subprocess.run(
            ["docker", "container", "inspect", fixture],
            check=True,
            capture_output=True,
            timeout=20,
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", fixture],
            check=True,
            capture_output=True,
            timeout=20,
        )
