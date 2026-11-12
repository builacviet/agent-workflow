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
import time
from uuid import uuid4

import pytest

from agent_workflow import budget
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.runners import coder_runtime, sandbox, sandbox_watchdog
from agent_workflow.runners.sandbox import SandboxManager, SandboxPolicy, SandboxRunResult


_FIXTURE_PIN = "sha256:" + "b" * 64
_ENGINE_INFO = b'"fixture-daemon-a" "linux" true true true true ["name=seccomp,profile=builtin"]'


_FIXTURE_HOST = "unix:///fixture-original.sock"
_OTHER_HOST = "unix:///fixture-other.sock"


@pytest.fixture(autouse=True)
def offline_docker_endpoint(monkeypatch, request):
    if "live_sandbox" not in request.fixturenames:
        monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
        monkeypatch.setenv("DOCKER_HOST", _FIXTURE_HOST)


def _image_metadata(image_id):
    return json.dumps([{
        "Id": image_id,
        "Os": "linux",
        "Config": {},
    }]).encode()


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
        return b'"fixture-daemon-a" "linux" true false true true ["name=seccomp,profile=builtin"]'

    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="cannot enforce"):
        SandboxManager(tmp_path / "fences").run(
            SandboxPolicy(),
            dispatch_id="a" * 32,
            workspace=tmp_path,
            image="sandbox-fixture",
            approved_image_id=_FIXTURE_PIN,
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


@pytest.mark.parametrize(
    "outcome", ["success", "timeout", "watchdog-race", "rm-failure", "image-mismatch"]
)
def test_docker_argument_boundary_and_cleanup(monkeypatch, tmp_path, outcome):
    identifier = "b" * 64
    dispatch = "a" * 32
    calls = []
    owned = None
    removal_pending = False

    def docker(args):
        nonlocal owned, removal_pending
        # Cleanup must still be possible after the execution deadline.
        if outcome in {"timeout", "watchdog-race", "rm-failure"}:
            budget.check_deadline()
        calls.append(args)
        match args[0]:
            case "info":
                return _ENGINE_INFO
            case "image":
                return _image_metadata(_FIXTURE_PIN)
            case "create":
                owned = args[args.index("--name") + 1]
                return identifier.encode()
            case "container" if args[1] == "inspect":
                assert args[-1] == identifier
                image_id = "sha256:" + "c" * 64 if outcome == "image-mismatch" else _FIXTURE_PIN
                return " ".join(
                    json.dumps(value)
                    for value in (identifier, "/" + owned, image_id, dispatch)
                ).encode()
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
                if outcome in {"watchdog-race", "rm-failure"}:
                    if outcome == "watchdog-race":
                        # Docker still lists the ID while the watchdog removes it.
                        removal_pending = True
                    return coder_runtime.docker_command(["docker", *args])
                owned = None
                return b""
        raise AssertionError(args)

    def wait_for_docker(_seconds):
        nonlocal owned
        if outcome == "rm-failure":
            raise TimeoutError("fixture cleanup verification budget expired")
        assert removal_pending
        owned = None

    def failed_rm(args, **kwargs):
        assert args == ["docker", "--host", _FIXTURE_HOST, "rm", "-f", identifier]
        raise subprocess.CalledProcessError(1, args, stderr=b"private Docker output")

    def attach(args, stdin, remaining):
        assert args == ["docker", "start", "-ai", identifier]
        assert stdin == "task input"
        assert 0 < remaining <= 10
        assert outcome != "image-mismatch", "mismatched image must never start"
        if outcome in {"timeout", "watchdog-race", "rm-failure"}:
            # Simulate real expiration immediately before Controller cleanup.
            budget._BUDGET.set((0.0, 0.0))
            raise TimeoutError("fixture deadline exceeded")
        return b"fixture output"

    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(coder_runtime, "run_container", attach)
    monkeypatch.setattr(coder_runtime.subprocess, "run", failed_rm)
    monkeypatch.setattr(sandbox.time, "sleep", wait_for_docker)
    monkeypatch.setattr(SandboxManager, "_start_watchdog", lambda *args: None)

    def run():
        return SandboxManager(tmp_path / "fences").run(
            SandboxPolicy(workspace_writable=False, timeout_seconds=10),
            dispatch_id=dispatch,
            workspace=tmp_path,
            image="sandbox-fixture",
            approved_image_id=_FIXTURE_PIN,
            command=("code", "--privileged"),
            stdin="task input",
            env=(("MODEL", "--network=host"),),
        )

    if outcome == "success":
        assert run() == SandboxRunResult(b"fixture output", _FIXTURE_PIN)
    elif outcome in {"timeout", "watchdog-race"}:
        with pytest.raises(TimeoutError, match="fixture deadline exceeded"):
            run()
    elif outcome == "rm-failure":
        with pytest.raises(WorkflowError, match=r"^Docker rm failed \(exit 1\)$"):
            run()
        assert owned is not None
        assert (tmp_path / "fences" / f"{dispatch}.scope").exists()
        return
    else:
        with pytest.raises(WorkflowError, match="container image differs from approved pin"):
            run()

    expected = ["image", "create", "container", "container", "rm", "container"]
    if outcome == "watchdog-race":
        expected.extend(["container", "container"])
    operations = [args for args in calls if args[0] != "info"]
    assert [args[0] for args in operations] == expected
    assert operations[2][1] == "inspect"
    create = operations[1]
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


def test_retagged_image_does_not_create_container(monkeypatch, tmp_path):
    calls = []

    def docker(args):
        calls.append(args)
        if args[0] == "info":
            return _ENGINE_INFO
        assert args == ["image", "inspect", "fixture:approved"]
        return _image_metadata("sha256:" + "c" * 64)

    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="image reference differs from approved pin"):
        SandboxManager(tmp_path / "fences").run(
            SandboxPolicy(),
            dispatch_id="a" * 32,
            workspace=tmp_path,
            image="fixture:approved",
            approved_image_id=_FIXTURE_PIN,
            command=("code",),
        )
    assert [args[0] for args in calls] == ["info", "image"]


def test_late_create_is_fenced_until_owned_container_appears(monkeypatch, tmp_path):
    dispatch = "a" * 32
    identifier = "b" * 64
    fences = tmp_path / "fences"
    requested_name = None
    present = False
    removed = []

    def docker(args):
        nonlocal requested_name, present
        match args[0]:
            case "info":
                return _ENGINE_INFO
            case "image":
                return _image_metadata(_FIXTURE_PIN)
            case "create":
                requested_name = args[args.index("--name") + 1]
                assert (fences / dispatch).read_text() == requested_name
                raise WorkflowError("create client timed out")
            case "container":
                assert args[1] == "ls"
                if present:
                    return json.dumps({
                        "ID": identifier,
                        "Names": requested_name,
                        "Labels": f"org.agent-workflow.sandbox.dispatch={dispatch}",
                    }).encode()
                return b""
            case "rm":
                assert args == ["rm", "-f", identifier]
                # Losing settlement evidence during removal must remain safe.
                assert not (fences / dispatch).exists()
                removed.append(identifier)
                present = False
                return b""
        raise AssertionError(args)

    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="create remains uncertain"):
        SandboxManager(fences).run(
            SandboxPolicy(),
            dispatch_id=dispatch,
            workspace=tmp_path,
            image="sandbox-fixture",
            approved_image_id=_FIXTURE_PIN,
            command=("code",),
        )

    fresh = SandboxManager(fences)
    with pytest.raises(WorkflowError, match="create remains uncertain"):
        fresh.stop(dispatch)
    assert (fences / dispatch).read_text() == requested_name
    assert not removed

    present = True
    fresh.stop(dispatch)
    assert removed == [identifier]
    assert not (fences / dispatch).exists()
    fresh.stop(dispatch)


def test_cleanup_rejects_unowned_id(monkeypatch, tmp_path):
    calls = []

    def docker(args):
        calls.append(args)
        return json.dumps({
            "ID": "b" * 64,
            "Names": "unrelated",
            "Labels": "",
        }).encode()

    manager = SandboxManager(tmp_path / "fences")
    dispatch = "a" * 32
    manager._record_scope(
        manager._scope_path(dispatch), dispatch, "unused",
        "fixture-daemon-a", time.time(), _FIXTURE_HOST,
    )
    monkeypatch.setattr(sandbox, "_require_engine", lambda: "fixture-daemon-a")
    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="verify sandbox ownership"):
        manager.stop(dispatch)
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
    manager = SandboxManager(tmp_path / "fences")
    # Opt-in fixtures supply a trusted image; freeze its current identity before use.
    pin = sandbox._local_image(image)

    def run(script, *args, policy=None):
        result = manager.run(
            policy or SandboxPolicy(timeout_seconds=20),
            dispatch_id=dispatch,
            workspace=workspace,
            image=image,
            approved_image_id=pin,
            command=("python", "-c", script, *args),
            phase="test",
        )
        assert result.image_id == pin
        return result.output

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



def test_image_with_unapproved_secret_env_is_rejected(monkeypatch):
    def docker(args):
        return json.dumps([{
            "Id": _FIXTURE_PIN, "Os": "linux",
            "Config": {"Env": ["PATH=/usr/bin", "OPENAI_API_KEY=synthetic-canary"]}
        }]).encode()

    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="local Linux image"):
        sandbox._local_image("fixture:secret")



def test_wrong_daemon_cannot_release_running_dispatch(monkeypatch, tmp_path):
    dispatch = "a" * 32
    manager = SandboxManager(tmp_path / "fences")
    scope = manager._scope_path(dispatch)
    name = f"my-wfc-sandbox-{dispatch}-code-" + "b" * 32
    identifier = "c" * 64
    manager._record_scope(scope, dispatch, name, "daemon-A-123", 1000000., _FIXTURE_HOST)
    manager._update_scope(scope, "CREATED")
    engine = {"id": "daemon-B-456", "owned": True, "removed": False}

    def docker(args):
        if args[:2] == ["container", "ls"]:
            if engine["id"] == "daemon-A-123" and engine["owned"]:
                return json.dumps({
                    "ID": identifier, "Names": name,
                    "Labels": f"org.agent-workflow.sandbox.dispatch={dispatch}",
                }).encode()
            return b""
        if args[:2] == ["rm", "-f"]:
            assert engine["id"] == "daemon-A-123"
            engine["owned"] = False
            engine["removed"] = True
            return b""
        raise AssertionError(args)

    monkeypatch.setattr(sandbox, "_require_engine", lambda: engine["id"])
    monkeypatch.setattr(sandbox, "_docker", docker)
    with pytest.raises(WorkflowError, match="daemon identity changed"):
        manager.stop(dispatch)
    assert scope.exists() and not engine["removed"]
    engine["id"] = "daemon-A-123"
    manager.stop(dispatch)
    assert engine["removed"] and not scope.exists()


def test_prepared_not_submitted_can_be_reconciled(monkeypatch, tmp_path):
    dispatch = "a" * 32
    manager = SandboxManager(tmp_path / "fences")
    fence = manager._fence_path(dispatch)
    scope = manager._scope_path(dispatch)
    name = f"my-wfc-sandbox-{dispatch}-code-" + "b" * 32
    manager._record_scope(scope, dispatch, name, "daemon-A-123", 1000000., _FIXTURE_HOST)
    manager._record_create(fence, name)
    monkeypatch.setattr(sandbox, "_require_engine", lambda: "daemon-A-123")
    monkeypatch.setattr(sandbox, "_docker", lambda args: b"")
    manager.stop(dispatch)
    assert not fence.exists() and not scope.exists()



def test_watchdog_recovers_from_daemon_error_after_controller_death(live_sandbox, tmp_path):
    """Kill the launching process and inject one daemon failure in the detached watchdog."""
    import sys
    import time

    _, _, _, workspace, image = live_sandbox
    dispatch = uuid4().hex
    fences = tmp_path / "independent-fences"
    pin = sandbox._local_image(image)
    code = """\
import sys
from pathlib import Path
from agent_workflow.runners.sandbox import SandboxManager, SandboxPolicy
manager = SandboxManager(Path(sys.argv[1]))
manager.run(
    SandboxPolicy(timeout_seconds=8), dispatch_id=sys.argv[2],
    workspace=Path(sys.argv[3]).resolve(), image=sys.argv[4],
    approved_image_id=sys.argv[5],
    command=("python", "-c", "from pathlib import Path; import time; "
             "Path('child-started').touch(); time.sleep(60)"),
)
"""
    # The watchdog inherits only its bootstrap paths. A test-only site hook
    # injects an expiry-time info failure in that process, without disrupting Docker.
    scope = fences / (dispatch + ".scope")
    fault = tmp_path / "watchdog-fault-observed"
    hook = tmp_path / "watchdog-hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(f"""
import os
import sys
from pathlib import Path

if "agent_workflow.runners.sandbox_watchdog" in sys.orig_argv:
    from agent_workflow.integrations.github import WorkflowError
    from agent_workflow.runners import sandbox

    original = sandbox._docker
    ready = Path({str(scope.with_name(scope.name + ".ready"))!r})
    fault = Path({str(fault)!r})

    def injected(args):
        if args[0] == "info" and ready.exists() and not fault.exists():
            assert "WFC_SYNTHETIC_CREDENTIAL" not in os.environ
            assert "HTTPS_PROXY" not in os.environ
            fault.touch()
            raise WorkflowError("synthetic watchdog daemon outage")
        return original(args)

    sandbox._docker = injected
""", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(hook), env.get("PYTHONPATH"))))
    env["WFC_SYNTHETIC_CREDENTIAL"] = "synthetic-canary"
    env["HTTPS_PROXY"] = "http://fixture:synthetic-canary@proxy.invalid"
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(fences), dispatch, str(workspace), image, pin],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    try:
        started = workspace / "child-started"
        deadline = time.monotonic() + 9
        while not started.exists() and time.monotonic() < deadline:
            assert child.poll() is None, "controller failed to launch workload"
            time.sleep(0.15)
        assert started.exists(), "workload failed to begin"
        child.kill()
        child.wait(timeout=5)
        scope = fences / (dispatch + ".scope")
        assert scope.exists(), "trusted launch scope must survive Controller death"
        deadline = time.monotonic() + 13
        while time.monotonic() < deadline:
            found = subprocess.run(
                ["docker", "container", "ls", "-aq", "--no-trunc", "--filter",
                 f"label=org.agent-workflow.sandbox.dispatch={dispatch}"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if not found:
                break
            time.sleep(0.25)
        assert not found, "independent watchdog did not terminate workload"
        assert fault.exists(), "detached watchdog did not exercise the injected outage"
        assert scope.exists(), "watchdog must not release Controller's durable fence"
        SandboxManager(fences).stop(dispatch)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_switch_during_create_cleans_original_and_never_attaches(monkeypatch, tmp_path):
    dispatch = "a" * 32
    identifier = "c" * 64
    owned = None
    calls = []

    def docker(args):
        nonlocal owned
        invocation, env = sandbox._docker_invocation(["docker", *args])
        assert invocation[1:3] == ["--host", _FIXTURE_HOST]
        assert "DOCKER_HOST" not in env and "DOCKER_CONTEXT" not in env
        calls.append(args[0])
        if args[0] == "info":
            return _ENGINE_INFO
        if args[0] == "image":
            return _image_metadata(_FIXTURE_PIN)
        if args[0] == "create":
            owned = args[args.index("--name") + 1]
            monkeypatch.setenv("DOCKER_HOST", _OTHER_HOST)
            return identifier.encode()
        if args[:2] == ["container", "inspect"]:
            return " ".join(map(json.dumps, (identifier, "/" + owned, _FIXTURE_PIN, dispatch))).encode()
        if args[:2] == ["container", "ls"]:
            if owned is None:
                return b""
            return json.dumps({
                "ID": identifier, "Names": owned,
                "Labels": f"org.agent-workflow.sandbox.dispatch={dispatch}",
            }).encode()
        if args == ["rm", "-f", identifier]:
            owned = None
            return b""
        raise AssertionError(args)

    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(SandboxManager, "_start_watchdog", lambda *args: None)
    monkeypatch.setattr(coder_runtime, "run_container", lambda *args: pytest.fail("attached after switch"))
    manager = SandboxManager(tmp_path / "fences")
    with pytest.raises(WorkflowError, match="endpoint changed during execution"):
        manager.run(
            SandboxPolicy(), dispatch_id=dispatch, workspace=tmp_path,
            image="fixture", approved_image_id=_FIXTURE_PIN, command=("code",),
        )
    assert "rm" in calls and owned is None
    assert not manager._scope_path(dispatch).exists()


@pytest.mark.parametrize("failure", ["info", "inventory", "persistent"])
def test_detached_watchdog_retries_or_retains_scope(monkeypatch, tmp_path, failure):
    dispatch = "a" * 32
    identifier = "c" * 64
    name = f"my-wfc-sandbox-{dispatch}-code-" + "b" * 32
    manager = SandboxManager(tmp_path / "fences")
    scope = manager._scope_path(dispatch)
    manager._record_scope(scope, dispatch, name, "fixture-daemon-a", time.time() - 1, _FIXTURE_HOST)
    manager._update_scope(scope, "CREATED")
    monkeypatch.setenv("DOCKER_HOST", _OTHER_HOST)
    monkeypatch.setattr(sandbox_watchdog.time, "sleep", lambda _: None)
    info_calls = inventory_calls = 0
    owned = True

    def docker(args):
        nonlocal info_calls, inventory_calls, owned
        assert sandbox._DOCKER_HOST.get() == _FIXTURE_HOST
        if args[0] == "info":
            info_calls += 1
            if info_calls > 1 and (failure == "persistent" or (failure == "info" and info_calls == 2)):
                raise WorkflowError("synthetic daemon outage")
            return _ENGINE_INFO
        if args[:2] == ["container", "ls"]:
            inventory_calls += 1
            if failure == "inventory" and inventory_calls == 1:
                raise WorkflowError("synthetic inventory failure")
            return json.dumps({
                "ID": identifier, "Names": name,
                "Labels": f"org.agent-workflow.sandbox.dispatch={dispatch}",
            }).encode() if owned else b""
        if args == ["rm", "-f", identifier]:
            owned = False
            return b""
        raise AssertionError(args)

    monkeypatch.setattr(sandbox, "_docker", docker)
    sandbox_watchdog.enforce(scope, dispatch)
    assert scope.exists()
    stamp = scope.with_name(scope.name + ".expired")
    if failure == "persistent":
        assert info_calls == 4 and owned and not stamp.exists()
    else:
        assert not owned and stamp.read_text(encoding="ascii") == "deadline-cleanup-confirmed"


@pytest.mark.parametrize("outcome", ["not-launched", "rejected", "unknown", "timeout"])
def test_create_fence_clears_only_on_definitive_rejection(monkeypatch, tmp_path, outcome):
    dispatch = "a" * 32
    manager = SandboxManager(tmp_path / "fences")

    def create(args, **kwargs):
        if outcome == "not-launched":
            raise FileNotFoundError("synthetic missing CLI")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args, 1)
        stderr = (
            b"Error response from daemon: No such image: " + _FIXTURE_PIN.encode()
            if outcome == "rejected" else b"synthetic uncertain connection loss"
        )
        raise subprocess.CalledProcessError(1, args, stderr=stderr)

    def docker(args):
        if args[0] == "info":
            return _ENGINE_INFO
        if args[0] == "image":
            return _image_metadata(_FIXTURE_PIN)
        if args[0] == "create":
            return coder_runtime.docker_command(["docker", *args])
        assert args[:2] == ["container", "ls"]
        return b""

    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(coder_runtime.subprocess, "run", create)
    terminal = outcome in {"not-launched", "rejected"}
    expected = sandbox.DockerCreateRejected if terminal else WorkflowError
    with pytest.raises(expected) as error:
        manager.run(
            SandboxPolicy(), dispatch_id=dispatch, workspace=tmp_path,
            image="fixture", approved_image_id=_FIXTURE_PIN, command=("code",),
        )
    if not terminal:
        assert "create remains uncertain" in str(error.value)
    assert manager._fence_path(dispatch).exists() is not terminal
    assert manager._scope_path(dispatch).exists() is not terminal


@pytest.mark.parametrize("env", [
    (("OPENAI_API_KEY", "opaque-canary"),),
    (("MODEL", "https://fixture:synthetic-canary@proxy.invalid"),),
    (("Https_Proxy", "http://proxy.invalid"),),
])
def test_direct_sandbox_env_rejects_secrets_and_proxy_overrides(env):
    with pytest.raises(ValueError, match="invalid sandbox environment"):
        sandbox._environment(env)


def test_approved_image_env_name_cannot_carry_proxy_credentials(monkeypatch):
    image = json.loads(_image_metadata(_FIXTURE_PIN))
    image[0]["Config"]["Env"] = ["LANG=https://fixture:synthetic-canary@proxy.invalid"]
    monkeypatch.setattr(sandbox, "_docker", lambda args: json.dumps(image).encode())
    with pytest.raises(WorkflowError, match="local Linux image"):
        sandbox._local_image("fixture")


def test_live_sandbox_suppresses_docker_proxy_defaults(live_sandbox, tmp_path, monkeypatch):
    run, _, _, _, _ = live_sandbox
    host = sandbox._selected_host()
    config = tmp_path / "proxy-config"
    config.mkdir()
    (config / "config.json").write_text(json.dumps({"proxies": {"default": {
        "httpProxy": "http://fixture:synthetic-canary@proxy.invalid",
        "httpsProxy": "http://fixture:synthetic-canary@proxy.invalid",
        "noProxy": "synthetic-canary.invalid",
    }}}), encoding="utf-8")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", host)
    monkeypatch.setenv("DOCKER_CONFIG", str(config))
    assert run(
        "import os, sys; assert all(os.environ.get(k) == '' for k in sys.argv[1:]); print('ok')",
        *sandbox._PROXY_NAMES,
    ).strip() == b"ok"


def test_runtime_state_is_independent_of_database_and_survives_restart(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from agent_workflow.runners.coder_runtime import CoderRuntime

    state = tmp_path / "sandbox-state"
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(state))
    service = SimpleNamespace(repository=SimpleNamespace(db_path=str(tmp_path / "db.sqlite")))
    first = CoderRuntime({}, service)
    assert first.sandbox._fence_dir == state
    dispatch = "a" * 32
    first.sandbox.record_engine(dispatch, "unix:///fixture.sock", "fixture-daemon-a")
    dsn = "postgresql://user:secret-password@localhost/database"
    second = CoderRuntime({"database": dsn}, service)
    assert second.sandbox._fence_dir == state
    assert second.sandbox.has_launch_records(dispatch)
    assert second.sandbox.recovery_engine(dispatch) == (
        "unix:///fixture.sock", "fixture-daemon-a"
    )
    assert "secret-password" not in str(second.sandbox._fence_dir)


def test_runtime_refuses_to_abandon_legacy_fences(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from agent_workflow.runners.coder_runtime import CoderRuntime
    from agent_workflow.integrations.github import WorkflowError

    database = tmp_path / "state.sqlite"
    legacy = tmp_path / "state.sqlite.sandbox-fences"
    legacy.mkdir()
    (legacy / "pending.scope").write_text("pending", encoding="ascii")
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "new-state"))
    service = SimpleNamespace(repository=SimpleNamespace(db_path=str(database)))
    with pytest.raises(WorkflowError, match="operator recovery"):
        CoderRuntime({}, service)
    assert (legacy / "pending.scope").read_text() == "pending"


def test_runtime_without_state_uses_stable_default(monkeypatch):
    from agent_workflow.runners.coder_runtime import CoderRuntime

    monkeypatch.delenv("WFC_SANDBOX_STATE_DIR", raising=False)
    runtime = CoderRuntime({}, None)
    assert runtime.sandbox._fence_dir == Path.home() / ".agent-workflow" / "sandbox-fences"


def test_dsn_override_cannot_hide_repository_legacy_fences(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from agent_workflow.runners.coder_runtime import CoderRuntime

    database = tmp_path / "state.sqlite"
    legacy = tmp_path / "state.sqlite.sandbox-fences"
    legacy.mkdir()
    record = legacy / "pending.scope"
    record.write_text("pending", encoding="ascii")
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "new-state"))
    service = SimpleNamespace(repository=SimpleNamespace(db_path=str(database)))
    dsn = "postgresql://user:secret-password@localhost/database"
    with pytest.raises(WorkflowError, match="operator recovery") as error:
        CoderRuntime({"database": dsn}, service)
    assert "secret-password" not in str(error.value)
    assert record.read_text(encoding="ascii") == "pending"
