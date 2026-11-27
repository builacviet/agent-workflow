"""Architecture boundary regressions for local WFC and worker-owned publication."""

import inspect
import json
import subprocess
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from test_agent_runtime import assignment_for
from test_agent_workflow import spec
from test_runtime_plugins import FAKE_IMAGE_ID, FakePlugin

from agent_workflow.application.dispatch import run_dispatch_pass
from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.runtime.ports import RuntimeAdapters
from agent_workflow.application.runtime.services.agent_execution import (
    AgentExecutionService,
)
from agent_workflow.application.task.use_cases.register_task_handoff import (
    RegisterTaskHandoffUseCase,
)
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.runtime import docker_runtime, sandbox
from agent_workflow.infrastructure.runtime.codex_plugin import CodexCLIPlugin
from agent_workflow.infrastructure.runtime.docker_runtime import DockerRuntime
from agent_workflow.infrastructure.runtime.registry import (
    RuntimeRegistry,
    runtime_spec_from_config,
)
from agent_workflow.infrastructure.runtime.sandbox import SandboxRunResult
from agent_workflow.infrastructure.runtime.worker_payload import (
    worker_assignment_payload,
)
from agent_workflow.infrastructure.runtime.workspace import RuntimeWorkspace


@pytest.fixture
def isolated_sandbox_state(tmp_path):
    return sandbox.SandboxManager(tmp_path / "sandbox-fences")


def test_dispatch_and_runtime_do_not_own_worker_publication():
    assert "GitHubApp" not in inspect.getsource(run_dispatch_pass)
    assert not hasattr(DockerRuntime, "publish")
    assert not hasattr(AgentExecutionService, "publish")


class BoundaryState:
    def __init__(self):
        self.tasks = {}

    def provision_task(self, value):
        self.tasks[value["task_id"]] = value | {
            "status": "AWAITING_HANDOFF",
            "current_head_sha": None,
            "completed_review_rounds": 0,
        }

    def get_coder_assignment(self, task_id):
        return assignment_for(self.tasks[task_id])

    def get_review_context(self, task_id):
        return dict(self.tasks[task_id])


class BoundaryLedger:
    def __init__(self, state):
        self.state = state
        self.slot = None
        self.pre_launch = False
        self.done = set()
        self.finished_dispatch = None

    def claim(self, task_ids, timeout, *, track_pre_launch=False):
        import time
        import uuid

        if self.slot is not None:
            return None
        eligible = sorted(
            task
            for task in task_ids
            if task not in self.done and task in self.state.tasks
        )
        if not eligible:
            return None
        task_id = eligible[0]
        task = self.state.tasks[task_id]
        claim = DispatchClaim(
            task_id=task_id,
            dispatch_id=uuid.uuid4().hex,
            input_sha=task["current_head_sha"] or task["base_sha"],
            round_number=task["completed_review_rounds"],
            deadline=time.time() + timeout,
        )
        self.slot = (claim.dispatch_id, "ACTIVE", task_id)
        self.pre_launch = track_pre_launch
        return claim

    def is_pre_launch(self, dispatch_id):
        return bool(self.slot and self.slot[0] == dispatch_id and self.pre_launch)

    def retire_pre_launch(self, dispatch_id):
        assert self.slot and self.slot[0] == dispatch_id
        self.pre_launch = False

    def record_runtime_evidence(self, *args):
        assert self.slot and self.slot[0] == args[0]

    def block(self, dispatch_id):
        assert self.slot and self.slot[0] == dispatch_id
        self.slot = (dispatch_id, "BLOCKED", self.slot[2])

    def finish(self, dispatch_id, sha):
        assert self.slot and self.slot[0] == dispatch_id
        self.done.add(self.slot[2])
        self.finished_dispatch = dispatch_id
        self.slot = None

    def acknowledge(self, dispatch_id):
        assert self.slot and self.slot[0] == dispatch_id and self.slot[1] == "BLOCKED"
        self.done.add(self.slot[2])
        self.slot = None
        self.pre_launch = False


def injected_runtime(config, state, ledger, sandbox_manager, *, plugins=()):
    handoff = Mock(spec=RegisterTaskHandoffUseCase)

    def register(request):
        state.tasks[request.task_id].update(
            status="READY_IN_REVIEW", current_head_sha=request.new_head_sha
        )

    handoff.execute.side_effect = register
    runtime = DockerRuntime(
        config,
        dispatch=ledger,
        leadership_guard=lambda: True,
        sandbox=sandbox_manager,
    )
    execution = AgentExecutionService(
        adapter_factory=lambda _guard: RuntimeAdapters(
            runtime, RuntimeWorkspace(config)
        ),
        runtime_spec=lambda: runtime_spec_from_config(config),
        registry=RuntimeRegistry((CodexCLIPlugin(), *plugins)),
        assignment_payload=worker_assignment_payload,
        dispatch=ledger,
        ci=Mock(),
        handoff_provider=lambda: nullcontext(handoff),
        require_ci_tracking=True,
    )
    return runtime, execution


@pytest.mark.parametrize(
    ("failure", "runtime_name"),
    [
        (None, "codex"),
        ("test", "codex"),
        ("publish", "codex"),
        ("result", "codex"),
        ("pr_url", "codex"),
        (None, "fake"),
        ("unsupported runtime", "unregistered"),
        ("unsupported model", "codex"),
        ("unsupported effort", "codex"),
    ],
)
def test_dispatch_pass_only_launches_phases_and_receives_result(
    tmp_path, monkeypatch, failure, runtime_name, isolated_sandbox_state
):
    service = BoundaryState()
    service.provision_task(spec())
    task = {
        "worktree": str(tmp_path),
        "handoff_path": "docs/handoff.md",
        "write_paths": ["src", "docs"],
    }
    model = "gpt-6-luna" if runtime_name == "codex" else "fixture-model"
    if failure == "unsupported model":
        model = "unapproved-model"
    effort = (
        "medium" if runtime_name == "fake" or failure == "unsupported effort" else None
    )
    ledger = BoundaryLedger(service)
    runtime, execution = injected_runtime(
        {
            "tasks": {"TEST": task},
            "app_id": 1,
            "installation_id": 2,
            "model": model,
            "runtime": runtime_name,
            "provider": "openai" if runtime_name == "codex" else "fixture",
            "effort": effort,
            "runtime_image": "synthetic-image",
        },
        service,
        ledger,
        isolated_sandbox_state,
        plugins=(FakePlugin(),),
    )
    if runtime_name == "codex":
        # Exercise backwards compatibility with existing operator configs.
        runtime.config.pop("runtime")
        runtime.config.pop("provider")
    calls = []
    monkeypatch.setattr(
        docker_runtime, "_selected_host", lambda: "unix:///fixture.sock"
    )
    monkeypatch.setattr(sandbox, "_require_engine", lambda: "fixture-daemon-a")
    monkeypatch.setattr(docker_runtime, "_require_engine", lambda: "fixture-daemon-a")
    monkeypatch.setattr(runtime, "stop", lambda *args: calls.append("stop"))

    def docker(claim, stage, mode, prompt="", *, code_plan=None):
        if mode == "code":
            assert runtime_name == "codex"
            assert code_plan.command == ("code",)
            assert code_plan.env == (("WFC_CODEX_MODEL", "gpt-6-luna"),)
        calls.append(mode)
        if mode == failure:
            raise BusinessRuleError("phase failed")
        if mode == "prepare":
            (stage / "source").mkdir()
        if mode == "publish":
            data = json.loads(prompt)
            context = data["context"]
            result = {
                key: context[key]
                for key in ["task_id", "repository", "branch", "base_sha"]
            }
            result.update(
                input_sha=data["input_sha"],
                head_sha="b" * 40,
                pr_url="https://github.com/builacviet/agent-workflow/pull/7",
            )
            if failure == "result":
                result["branch"] = "unexpected"
            if failure == "pr_url":
                result["pr_url"] = (
                    "https://github.com/builacviet/techlead-review-poc/pull/7"
                )
            (stage / "result.json").write_text(json.dumps(result))
        return b"success"

    def sandbox_run(policy, **invocation):
        assert runtime_name == "fake"
        assert policy.network == "none"
        assert invocation["workspace"].name == "source"
        assert invocation["image"] == "synthetic-image"
        assert invocation["approved_image_id"] == FAKE_IMAGE_ID
        assert invocation["command"] == ("fake-exec", "fixture-model", "medium")
        assert invocation["env"] == ()
        calls.append("code")
        return SandboxRunResult(output=b"fixture", image_id=FAKE_IMAGE_ID)

    monkeypatch.setattr(runtime, "docker", docker)
    monkeypatch.setattr(runtime.sandbox, "run", sandbox_run)
    dispatch = _dispatch_pass(service, ledger, execution)
    if failure and failure.startswith("unsupported"):
        message = (
            "unsupported runtime$"
            if runtime_name == "unregistered"
            else "unsupported runtime provider, model or effort"
        )
        with pytest.raises(BusinessRuleError, match=message):
            dispatch.execute({"TEST"})
        assert ledger.claim({"TEST"}, 60) is None
        assert calls == []
        assert not (tmp_path / ".runtime").exists()
        return
    if failure:
        with pytest.raises(BusinessRuleError):
            dispatch.execute({"TEST"})
        assert ledger.claim({"TEST"}, 60) is None
    else:
        assert dispatch.execute({"TEST"})
        assert ledger.finished_dispatch is not None
        assert not runtime.sandbox.has_launch_records(ledger.finished_dispatch)
        assert service.get_review_context("TEST")["status"] == "READY_IN_REVIEW"
        assert [x for x in calls if x != "stop"] == [
            "prepare",
            "code",
            "test",
            "publish",
        ]
    assert service.get_review_context("TEST")["completed_review_rounds"] == 0
    assert calls[-1] == "stop"


@pytest.fixture
def recovery_dispatch(tmp_path):
    database = tmp_path / "state.db"
    service = BoundaryState()
    service.provision_task(spec())
    service.provision_task(spec("NEXT"))
    config = {"database": str(database), "tasks": {"TEST": {}}, "model": "gpt-6-luna"}
    return config, service, BoundaryLedger(service)


def _blocked_dispatch(ledger):
    assert ledger.slot is not None and ledger.slot[1] == "BLOCKED"
    return ledger.slot[0]


def _dispatch_pass(service, ledger, execution):
    leadership = Mock()
    leadership.try_acquire.return_value = True
    leadership.is_held.return_value = True
    return RunDispatchPassUseCase(
        leadership,
        ledger,
        service,
        execution,
    )


@pytest.mark.parametrize("failure", ["invalid-model", "docker-unavailable"])
def test_worker_reconciliation_accepts_proven_pre_launch_without_docker(
    recovery_dispatch, monkeypatch, failure, isolated_sandbox_state
):
    config, service, ledger = recovery_dispatch

    def no_docker(*args):
        pytest.fail(
            "reconciliation must not contact Docker for a proven pre-launch dispatch"
        )

    monkeypatch.setattr(sandbox, "_require_engine", no_docker)
    if failure == "invalid-model":
        config["model"] = "unsupported"
        monkeypatch.setattr(docker_runtime, "_selected_host", no_docker)
    else:
        monkeypatch.setattr(
            docker_runtime, "_selected_host", lambda: "unix:///fixture.sock"
        )

        def unavailable():
            raise BusinessRuleError("synthetic Docker outage")

        monkeypatch.setattr(docker_runtime, "_require_engine", unavailable)

    runtime, execution = injected_runtime(
        config, service, ledger, isolated_sandbox_state
    )
    with pytest.raises(
        BusinessRuleError, match="unsupported runtime|synthetic Docker outage"
    ):
        _dispatch_pass(service, ledger, execution).execute({"TEST"})
    dispatch_id = _blocked_dispatch(ledger)
    assert ledger.is_pre_launch(dispatch_id)
    assert ledger.claim({"NEXT"}, 60) is None
    runtime.reconcile(dispatch_id)
    assert ledger.claim({"NEXT"}, 60) is None
    ledger.acknowledge(dispatch_id)
    assert not ledger.is_pre_launch(dispatch_id)
    assert ledger.claim({"NEXT"}, 60).task_id == "NEXT"


@pytest.mark.parametrize("pin_written", [False, True])
def test_missing_pin_after_pre_launch_retirement_holds_slot(
    recovery_dispatch, monkeypatch, pin_written, isolated_sandbox_state
):
    config, service, ledger = recovery_dispatch
    runtime, execution = injected_runtime(
        config, service, ledger, isolated_sandbox_state
    )
    monkeypatch.setattr(
        docker_runtime, "_selected_host", lambda: "unix:///fixture.sock"
    )
    monkeypatch.setattr(docker_runtime, "_require_engine", lambda: "fixture-daemon-a")
    original = sandbox.SandboxManager.record_engine

    def record_pin(manager, dispatch_id, host, daemon_id):
        # A fresh connection must see the committed retirement before any pin write.
        assert not ledger.is_pre_launch(dispatch_id)
        if not pin_written:
            raise OSError("synthetic pin write failure")
        original(manager, dispatch_id, host, daemon_id)

    def uncertain(*args):
        raise BusinessRuleError("synthetic launch uncertainty")

    monkeypatch.setattr(sandbox.SandboxManager, "record_engine", record_pin)
    monkeypatch.setattr(execution, "_execute_phases", uncertain)
    with pytest.raises((OSError, BusinessRuleError), match="synthetic"):
        _dispatch_pass(service, ledger, execution).execute({"TEST"})
    dispatch_id = _blocked_dispatch(ledger)
    if pin_written:
        runtime.sandbox._engine_path(dispatch_id).unlink()
    assert not ledger.is_pre_launch(dispatch_id)
    with pytest.raises(BusinessRuleError, match="lacks its original Docker endpoint"):
        runtime.reconcile(dispatch_id)
    assert ledger.claim({"NEXT"}, 60) is None


def test_missing_pre_launch_marker_and_pin_holds_slot(
    recovery_dispatch, isolated_sandbox_state
):
    config, service, ledger = recovery_dispatch
    runtime, _ = injected_runtime(config, service, ledger, isolated_sandbox_state)
    claim = ledger.claim({"TEST"}, 60)
    dispatch_id = claim.dispatch_id
    ledger.block(dispatch_id)
    with pytest.raises(BusinessRuleError, match="lacks its original Docker endpoint"):
        runtime.reconcile(dispatch_id)
    assert ledger.claim({"NEXT"}, 60) is None


@pytest.mark.parametrize("suffix", [".engine", ".scope", ""])
def test_pre_launch_marker_cannot_override_existing_launch_records(
    recovery_dispatch, suffix, isolated_sandbox_state
):
    config, service, ledger = recovery_dispatch
    dispatch_id = ledger.claim({"TEST"}, 60, track_pre_launch=True).dispatch_id
    ledger.block(dispatch_id)
    runtime, _ = injected_runtime(config, service, ledger, isolated_sandbox_state)
    path = runtime.sandbox._engine_path(dispatch_id).with_suffix(suffix)
    path.parent.mkdir()
    path.touch()
    with pytest.raises(
        BusinessRuleError, match="pre-launch evidence conflicts with Docker records"
    ):
        runtime.reconcile(dispatch_id)
    assert ledger.claim({"NEXT"}, 60) is None


@pytest.mark.parametrize("definitive", [False, True])
def test_global_slot_release_requires_affirmative_create_settlement(
    tmp_path, monkeypatch, definitive, isolated_sandbox_state
):
    database = tmp_path / "state.db"
    config = {"database": str(database)}
    service = BoundaryState()
    service.provision_task(spec())
    service.provision_task(spec("NEXT"))
    ledger = BoundaryLedger(service)
    runtime, _ = injected_runtime(config, service, ledger, isolated_sandbox_state)
    dispatch_id = None
    name = None
    visible = False
    identifier = "a" * 64
    pin = "sha256:" + "b" * 64

    def docker(args):
        nonlocal name, visible
        if args[0] == "image":
            return json.dumps([{"Id": pin, "Os": "linux", "Config": {}}]).encode()
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            if definitive:
                raise sandbox.DockerCreateRejected("simulated definitive rejection")
            raise BusinessRuleError("simulated create timeout")
        if args[:2] == ["container", "ls"]:
            if visible:
                return json.dumps(
                    {
                        "ID": identifier,
                        "Names": name,
                        "Labels": f"org.agent-workflow.sandbox.dispatch={dispatch_id}",
                    }
                ).encode()
            return b""
        if args == ["rm", "-f", identifier]:
            assert visible
            visible = False
            return b""
        raise AssertionError(args)

    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///fixture.sock")
    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(sandbox, "_require_engine", lambda: "fixture-daemon-a")
    monkeypatch.setattr(sandbox, "docker_command", lambda args: b"")

    def execute(claim, _context, _guard):
        nonlocal dispatch_id
        dispatch_id = claim.dispatch_id
        ledger.retire_pre_launch(dispatch_id)
        runtime.sandbox.record_engine(
            dispatch_id, "unix:///fixture.sock", "fixture-daemon-a"
        )
        runtime.sandbox.run(
            sandbox.SandboxPolicy(),
            dispatch_id=dispatch_id,
            workspace=tmp_path.resolve(),
            image="fixture",
            approved_image_id=pin,
            command=("fixture",),
        )
        pytest.fail("an uncertain create must never start a workload")

    with pytest.raises(BusinessRuleError):
        _dispatch_pass(service, ledger, Mock(execute=execute)).execute({"TEST"})
    assert name is not None
    assert ledger.claim({"NEXT"}, 60) is None
    if not definitive:
        # Empty inventory is not proof an accepted request cannot appear later.
        with pytest.raises(BusinessRuleError, match="create remains uncertain"):
            runtime.reconcile(dispatch_id)
        assert ledger.claim({"NEXT"}, 60) is None
        visible = True
    # Operator acknowledgment is still required even for definitive rejection.
    runtime.reconcile(dispatch_id)
    assert not visible
    assert ledger.claim({"NEXT"}, 60) is None
    ledger.acknowledge(dispatch_id)
    runtime.release_engine(dispatch_id)
    assert ledger.claim({"NEXT"}, 60).task_id == "NEXT"


def test_worker_owns_bot_commit_ordinary_push_and_draft(tmp_path, monkeypatch):
    import agent_workflow.workers.trusted_git.main as worker
    from agent_workflow.workers.trusted_git.app_auth import BOT

    root = tmp_path
    (root / "repo").mkdir()
    (root / "source").mkdir()
    monkeypatch.setattr(worker, "ROOT", root)
    calls = []
    monkeypatch.setattr(worker, "snapshot", lambda *args: {})
    monkeypatch.setattr(
        worker,
        "changes",
        lambda *args: [{"path": "src/a.py", "content": b"change", "mode": "100644"}],
    )

    def git(repo, *args):
        calls.append(args)
        if args[0] == "show":
            return (root / "repo" / args[1][1:]).read_bytes()
        return b"b" * 40 if args[0] == "rev-parse" else b""

    monkeypatch.setattr(worker, "git", git)
    monkeypatch.setattr(worker, "git_auth", lambda app, repo, *args: calls.append(args))
    monkeypatch.setattr(worker, "AppVerifier", lambda app: lambda *args: None)

    class App:
        def identity(self):
            return BOT, "123+" + BOT + "@users.noreply.github.com"

        def api(self, method, path, data=None):
            calls.append((method, path, data))
            return (
                []
                if method == "GET"
                else {
                    "draft": True,
                    "user": {"login": BOT},
                    "html_url": "https://github.com/builacviet/agent-workflow/pull/7",
                }
            )

    context = spec() | {"current_head_sha": None}
    worker.publish(
        {
            "context": context,
            "input_sha": context["base_sha"],
            "write_paths": ["src", "docs"],
            "handoff_path": "docs/handoff.md",
            "test_summary": "pytest exit 0",
        },
        App(),
    )
    push = next(call for call in calls if call[0] == "push")
    assert "--force" not in push and push[-1] == "HEAD:refs/heads/task/test"
    assert ("config", "user.name", BOT) in calls
    assert json.loads((root / "result.json").read_text())["head_sha"] == "b" * 40


@pytest.mark.parametrize("ignored", [False, True])
def test_worker_real_git_push_to_disposable_bare_remote(tmp_path, monkeypatch, ignored):
    import agent_workflow.workers.trusted_git.main as worker
    from agent_workflow.workers.trusted_git.app_auth import BOT
    from agent_workflow.workers.trusted_git.repository import git, snapshot

    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("before")
    if ignored:
        (repo / ".gitignore").write_text("src/ignored.py\ndocs/\n")
    git(repo, "add", ".")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    git(repo, "checkout", "-b", "task/test")
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "origin", "task/test")
    source = tmp_path / "source"
    source.mkdir()
    snapshot(repo, base, source)
    (source / "src/a.py").write_text("after")
    if ignored:
        (source / "src/ignored.py").write_text("tested ignored addition")
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker, "git_auth", lambda app, root, *args: git(root, *args))
    monkeypatch.setattr(worker, "AppVerifier", lambda app: lambda *args: None)

    class App:
        def identity(self):
            return BOT, "123+" + BOT + "@users.noreply.github.com"

        def api(self, method, path, data=None):
            return (
                []
                if method == "GET"
                else {
                    "draft": True,
                    "user": {"login": BOT},
                    "html_url": "https://github.com/builacviet/agent-workflow/pull/7",
                }
            )

    worker.publish(
        {
            "context": spec() | {"base_sha": base},
            "input_sha": base,
            "write_paths": ["src", "docs"],
            "handoff_path": "docs/handoff.md",
            "test_summary": "synthetic test evidence",
        },
        App(),
    )
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    assert head != base
    assert git(bare, "rev-parse", "refs/heads/task/test").decode().strip() == head
    assert (
        git(repo, "log", "-1", "--format=%an|%ae").decode().strip()
        == BOT + "|123+" + BOT + "@users.noreply.github.com"
    )
    assert (repo / "docs/handoff.md").exists()
    assert (
        git(repo, "show", "HEAD:docs/handoff.md")
        == (repo / "docs/handoff.md").read_bytes()
    )
    if ignored:
        assert git(repo, "show", "HEAD:src/ignored.py") == b"tested ignored addition"


def test_source_digest_frames_paths_and_contents(tmp_path):
    from agent_workflow.infrastructure.runtime.workspace import source_digest

    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "a").write_bytes(b"bc")
    (right / "ab").write_bytes(b"c")
    assert b"a" + b"bc" == b"ab" + b"c"  # old unframed input collision
    assert source_digest(left) != source_digest(right)
    (right / "ab").unlink()
    (right / "a").write_bytes(b"bc")
    assert source_digest(left) == source_digest(right)


def test_controller_compose_parses_without_starting_services():
    import shutil
    from pathlib import Path

    if shutil.which("docker") is None:
        pytest.skip("Docker CLI needed for compose syntax validation")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(root / "compose.yaml"),
            "config",
            "--no-env-resolution",
            "--quiet",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=20,
    )


def test_network_disabled_test_launch_overrides_proxy_defaults(
    tmp_path, monkeypatch, isolated_sandbox_state
):
    service = BoundaryState()
    ledger = BoundaryLedger(service)
    runtime, _ = injected_runtime(
        {"image": "fixture"}, service, ledger, isolated_sandbox_state
    )
    dispatch = "a" * 32
    host = "unix:///fixture.sock"
    monkeypatch.setattr(docker_runtime, "_selected_host", lambda: host)
    monkeypatch.setattr(sandbox, "_selected_host", lambda: host)
    monkeypatch.setattr(sandbox, "_require_engine", lambda: "fixture-daemon-a")
    monkeypatch.setenv("HTTPS_PROXY", "http://fixture:synthetic-canary@proxy.invalid")
    monkeypatch.setenv("DOCKER_CONTEXT", "some-other-context")
    runtime.sandbox.record_engine(dispatch, host, "fixture-daemon-a")

    def run(args, prompt, remaining):
        assert "--network=none" in args
        assert {
            args[index + 1] for index, arg in enumerate(args) if arg == "--env"
        } >= {name + "=" for name in sandbox._PROXY_NAMES}
        invocation, env = sandbox._docker_invocation(args)
        assert invocation[:3] == ["docker", "--host", host]
        assert "HTTPS_PROXY" not in env and "DOCKER_CONTEXT" not in env
        assert all("synthetic-canary" not in arg for arg in invocation)
        return b"synthetic test output"

    monkeypatch.setattr(sandbox, "run_container", run)
    assert (
        runtime.docker(
            DispatchClaim(
                dispatch, "TEST", "a" * 40, docker_runtime.time.time() + 30, 0
            ),
            tmp_path,
            "test",
        )
        == b"synthetic test output"
    )
