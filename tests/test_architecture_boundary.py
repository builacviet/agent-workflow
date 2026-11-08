"""Architecture boundary regressions for local WFC and worker-owned publication."""

from contextlib import contextmanager
import inspect
import json
import socket
import subprocess

import pytest

from agent_workflow.service import WorkflowService
from agent_workflow.github import WorkflowError
from agent_workflow.controller import DispatchLedger, run_once
from agent_workflow.coder_runtime import CoderRuntime
from test_my_wfc import spec, payload


def test_wfc_full_lifecycle_needs_no_network_or_subprocess(
    tmp_path, monkeypatch
):
    def denied(*args, **kwargs):
        raise AssertionError("WFC attempted external access")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    service = WorkflowService(tmp_path / "state.db")
    service.provision_task(spec())
    head = "a" * 40
    service.register_task_handoff(
        "TEST",
        spec()["repository"],
        "task/test",
        spec()["base_sha"],
        head,
        "docs/handoff.md",
        "worker reported tests passed",
    )
    claim = service.claim_review("TEST", head, "lead")
    service.submit_review(payload(claim, "APPROVED"), "lead")
    result = service.get_review_result("TEST")
    assert result["approval_current"] is None
    assert result["remote_verification"] == "not_performed_by_wfc"
    assert service.submit_review(payload(claim, "APPROVED"), "lead")[
        "duplicate"
    ]
    assert service.get_coder_assignment("TEST")["completed_review_rounds"] == 1


def test_service_has_no_sql_or_external_adapter():
    import agent_workflow.service as service

    text = inspect.getsource(service)
    assert "sqlite3" not in text and ".execute(" not in text
    assert "verify_remote" not in text and "coder_worker" not in text
    import agent_workflow.controller as controller

    assert "GitHubApp" not in inspect.getsource(controller)
    assert not hasattr(CoderRuntime, "publish")


class MemoryUnit:
    def __init__(self):
        self.tasks = {}
        self.events = []

    def find_authorization(self, values):
        task = self.tasks.get(values[0])
        return {"metadata": task["metadata"]} if task else None

    def insert_task(self, values):
        keys = [
            "task_id",
            "metadata",
            "repository",
            "branch",
            "base_sha",
            "requested_effort",
            "require_verified_effort",
            "status",
            "created_at",
            "updated_at",
        ]
        self.tasks[values[0]] = dict(zip(keys, values))

    def insert_event(self, values):
        self.events.append(values)

    def set_status(self, values):
        status, timestamp, task_id = values
        self.tasks[task_id].update(status=status, updated_at=timestamp)


class MemoryRepository:
    def __init__(self):
        self.unit = MemoryUnit()

    @contextmanager
    def transaction(self):
        yield self.unit

    @contextmanager
    def read(self):
        yield self.unit


def test_service_accepts_repository_interface_without_sqlite(tmp_path):
    repo = MemoryRepository()
    service = WorkflowService(repository=repo)
    service.provision_task(spec())
    service.provision_task(spec())
    assert len(repo.unit.tasks) == 1 and len(repo.unit.events) == 1
    with pytest.raises(WorkflowError):
        service.provision_task(spec() | {"title": "conflicting authorization"})


@pytest.mark.parametrize("failure", [None, "test", "publish", "result", "pr_url"])
def test_controller_only_launches_phases_and_receives_result(
    tmp_path, monkeypatch, failure
):
    service = WorkflowService(tmp_path / "state.db")
    service.provision_task(spec())
    task = {
        "worktree": str(tmp_path),
        "handoff_path": "docs/handoff.md",
        "write_paths": ["src", "docs"],
    }
    runtime = CoderRuntime(
        {"tasks": {"TEST": task}, "app_id": 1, "installation_id": 2}, service
    )
    calls = []
    monkeypatch.setattr(runtime, "stop", lambda *args: calls.append("stop"))

    def docker(claim, stage, mode, prompt=""):
        calls.append(mode)
        if mode == failure:
            raise WorkflowError("phase failed")
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
                result["pr_url"] = "https://github.com/builacviet/techlead-review-poc/pull/7"
            (stage / "result.json").write_text(json.dumps(result))
        return b"success"

    monkeypatch.setattr(runtime, "docker", docker)
    ledger = DispatchLedger(service)
    if failure:
        with pytest.raises(WorkflowError):
            run_once(service, ledger, {"TEST"}, runtime.execute)
        assert ledger.claim({"TEST"}, 60) is None
    else:
        assert run_once(service, ledger, {"TEST"}, runtime.execute)
        assert (
            service.get_review_context("TEST")["status"] == "READY_IN_REVIEW"
        )
        assert [x for x in calls if x != "stop"] == [
            "prepare",
            "code",
            "test",
            "publish",
        ]
    assert service.get_review_context("TEST")["completed_review_rounds"] == 0
    assert calls[-1] == "stop"


def test_worker_owns_bot_commit_ordinary_push_and_draft(tmp_path, monkeypatch):
    import coder_worker.main as worker
    from coder_worker.app_auth import BOT

    root = tmp_path
    (root / "repo").mkdir()
    (root / "source").mkdir()
    monkeypatch.setattr(worker, "ROOT", root)
    calls = []
    monkeypatch.setattr(worker, "snapshot", lambda *args: {})
    monkeypatch.setattr(
        worker,
        "changes",
        lambda *args: [
            {"path": "src/a.py", "content": b"change", "mode": "100644"}
        ],
    )

    def git(repo, *args):
        calls.append(args)
        if args[0] == "show":
            return (root / "repo" / args[1][1:]).read_bytes()
        return b"b" * 40 if args[0] == "rev-parse" else b""

    monkeypatch.setattr(worker, "git", git)
    monkeypatch.setattr(
        worker, "git_auth", lambda app, repo, *args: calls.append(args)
    )
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
    assert (
        json.loads((root / "result.json").read_text())["head_sha"] == "b" * 40
    )


@pytest.mark.parametrize("ignored", [False, True])
def test_worker_real_git_push_to_disposable_bare_remote(
    tmp_path, monkeypatch, ignored
):
    import coder_worker.main as worker
    from coder_worker.repository import git, snapshot
    from coder_worker.app_auth import BOT

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
    monkeypatch.setattr(
        worker, "git_auth", lambda app, root, *args: git(root, *args)
    )
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
    assert (
        git(bare, "rev-parse", "refs/heads/task/test").decode().strip() == head
    )
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
        assert (
            git(repo, "show", "HEAD:src/ignored.py")
            == b"tested ignored addition"
        )


def test_source_digest_frames_paths_and_contents(tmp_path):
    from agent_workflow.coder_runtime import source_digest

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
    from pathlib import Path
    import shutil

    if shutil.which("docker") is None:
        pytest.skip("Docker CLI needed for compose syntax validation")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(root / "compose.controller.yaml"),
            "config",
            "--no-env-resolution",
            "--quiet",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=20,
    )
