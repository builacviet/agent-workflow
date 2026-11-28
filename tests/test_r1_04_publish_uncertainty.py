"""R1-04A: offline characterization of ambiguous GitHub publish outcomes.

These tests inject an error *after* a simulated remote write was accepted.
They prove local fail-closed behavior, not remote receipt verification, durable
publication intent, real GitHub races, or atomicity across local storage and GitHub.
"""

from __future__ import annotations

import pytest

from agent_workflow.application.exceptions import (
    RuntimeAdmissionError,
    RuntimeContractError,
    RuntimeExecutionError,
)
from agent_workflow.domain.workflow_identity import REPOSITORY
from agent_workflow.workers.trusted_git.app_auth import BOT

BASE = "a" * 40
PUBLISHED = "b" * 40
BRANCH = "codex/r1-04-uncertain"
HANDOFF = "docs/handoff.md"


@pytest.mark.parametrize("ambiguous_step", ["push", "post", "patch"])
def test_publish_uncertainty_never_emits_success_receipt(
    tmp_path,
    monkeypatch,
    ambiguous_step,
):
    """Lost push/POST/PATCH response cannot become a trusted result.json."""
    import agent_workflow.workers.trusted_git.main as worker

    root = tmp_path
    (root / "repo").mkdir()
    (root / "source").mkdir()
    monkeypatch.setattr(worker, "ROOT", root)
    monkeypatch.setattr(worker, "snapshot", lambda *args: {})
    monkeypatch.setattr(
        worker,
        "changes",
        lambda *args: [
            {"path": "src/fixture.py", "content": b"checked fixture", "mode": "100644"},
        ],
    )
    git_calls = []
    api_calls = []

    def fake_git(repo, *args):
        git_calls.append(args)
        if args[0] == "show":
            return (root / "repo" / args[1][1:]).read_bytes()
        if args[0] == "rev-parse":
            return PUBLISHED.encode()
        return b""

    def fake_git_auth(_app, _repo, *args):
        git_calls.append(args)
        if args[0] == "push" and ambiguous_step == "push":
            # Simulate a push accepted remotely but acknowledgement lost.
            raise RuntimeExecutionError("uncertain Git push response")
        return b""

    monkeypatch.setattr(worker, "git", fake_git)
    monkeypatch.setattr(worker, "git_auth", fake_git_auth)
    monkeypatch.setattr(worker, "AppVerifier", lambda _app: lambda *args: None)

    class FakeApp:
        def identity(self):
            return BOT, "123+" + BOT + "@users.noreply.github.com"

        def api(self, method, endpoint, payload=None):
            api_calls.append((method, endpoint))
            if method == "GET":
                if ambiguous_step == "patch":
                    return [
                        {
                            "draft": True,
                            "number": 7,
                            "user": {"login": BOT},
                        }
                    ]
                return []
            # Remote request was hypothetically accepted, response was lost.
            raise RuntimeExecutionError("uncertain PR write response")

    with pytest.raises(RuntimeExecutionError, match="uncertain"):
        worker.publish(
            {
                "context": {
                    "task_id": "R104",
                    "repository": REPOSITORY,
                    "branch": BRANCH,
                    "base_sha": BASE,
                    "context_paths": [HANDOFF],
                },
                "input_sha": BASE,
                "write_paths": ["src", "docs"],
                "handoff_path": HANDOFF,
                "test_summary": "synthetic test evidence",
            },
            FakeApp(),
        )

    assert not (root / "result.json").exists()
    assert sum(call[0] == "push" for call in git_calls) == 1
    writes = [method for method, _ in api_calls if method in {"POST", "PATCH"}]
    assert writes == ([] if ambiguous_step == "push" else [ambiguous_step.upper()])
    # A test run does not retry side effects. Recovery needs independent proof.


def _publish_harness(
    tmp_path,
    monkeypatch,
    *,
    entries=None,
    existing_pr=None,
    write_response=None,
):
    import agent_workflow.workers.trusted_git.main as worker

    root = tmp_path
    (root / "repo").mkdir()
    (root / "source").mkdir()
    monkeypatch.setattr(worker, "ROOT", root)
    monkeypatch.setattr(worker, "snapshot", lambda *_args: {})
    monkeypatch.setattr(
        worker,
        "changes",
        lambda *_args: (
            entries
            if entries is not None
            else [{"path": "src/fixture.py", "content": b"checked", "mode": "100644"}]
        ),
    )
    git_calls = []
    api_calls = []

    def fake_git(repo, *args):
        git_calls.append(args)
        if args[0] == "show":
            return (repo / args[1][1:]).read_bytes()
        if args[0] == "rev-parse":
            return PUBLISHED.encode()
        return b""

    monkeypatch.setattr(worker, "git", fake_git)
    monkeypatch.setattr(
        worker, "git_auth", lambda _app, _repo, *args: git_calls.append(args) or b""
    )
    monkeypatch.setattr(worker, "AppVerifier", lambda _app: lambda *_args: None)

    class FakeApp:
        def identity(self):
            return BOT, "123+" + BOT + "@users.noreply.github.com"

        def api(self, method, endpoint, payload=None):
            api_calls.append((method, endpoint, payload))
            return existing_pr if method == "GET" else write_response

    data = {
        "context": {
            "task_id": "C5B-SYNTHETIC",
            "repository": REPOSITORY,
            "branch": BRANCH,
            "base_sha": BASE,
            "context_paths": [HANDOFF],
        },
        "input_sha": BASE,
        "write_paths": ["src", "docs"],
        "handoff_path": HANDOFF,
        "test_summary": "synthetic test evidence",
    }
    return worker, FakeApp(), data, git_calls, api_calls


def test_git_auth_failure_is_sanitized_runtime_execution_error(monkeypatch, tmp_path):
    import agent_workflow.workers.trusted_git.main as worker

    class App:
        def token(self):
            return "synthetic-token"

    def fail(*_args, **_kwargs):
        raise OSError("private synthetic Git output")

    monkeypatch.setattr(worker.subprocess, "run", fail)
    with pytest.raises(
        RuntimeExecutionError, match="worker Git operation failed"
    ) as error:
        worker.git_auth(App(), tmp_path, "push", "origin", "HEAD")
    assert error.value.__suppress_context__
    assert "private" not in str(error.value)


def test_prepare_rejects_remote_move_as_contract_error(monkeypatch, tmp_path):
    import agent_workflow.workers.trusted_git.main as worker

    monkeypatch.setattr(worker, "ROOT", tmp_path)
    calls = []

    def fake_git(_repo, *args):
        calls.append(args)
        return b"c" * 40 if args == ("rev-parse", "FETCH_HEAD") else b""

    monkeypatch.setattr(worker, "git", fake_git)
    monkeypatch.setattr(worker, "git_auth", lambda *_args: b"")
    monkeypatch.setattr(worker, "AppVerifier", lambda _app: lambda *_args: None)
    with pytest.raises(RuntimeContractError, match="remote moved"):
        worker.prepare(
            {
                "context": {"branch": BRANCH, "base_sha": BASE},
                "input_sha": BASE,
            },
            object(),
        )
    assert ("checkout", "-b", BRANCH, "FETCH_HEAD") not in calls
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize(
    "context",
    [
        {"repository": "other/repository", "branch": BRANCH},
        {"repository": REPOSITORY, "branch": "main"},
    ],
)
def test_main_rejects_unapproved_repository_or_branch_before_auth(monkeypatch, context):
    import json
    import sys
    from io import StringIO

    import agent_workflow.workers.trusted_git.main as worker

    monkeypatch.setattr(
        worker.sys,
        "stdin",
        StringIO(json.dumps({"context": context, "input_sha": BASE})),
    )
    monkeypatch.setattr(sys, "argv", ["trusted_git", "prepare"])
    monkeypatch.setattr(
        worker,
        "GitHubApp",
        lambda *_args: pytest.fail("unauthorized request reached credential setup"),
    )
    with pytest.raises(
        RuntimeAdmissionError, match="unauthorized repository or branch"
    ):
        worker.main()


def test_publish_rejects_unauthorized_handoff_before_git_or_api_effects(
    monkeypatch, tmp_path
):
    worker, app, data, git_calls, api_calls = _publish_harness(tmp_path, monkeypatch)
    data["context"]["context_paths"] = []
    with pytest.raises(RuntimeAdmissionError, match="unauthorized handoff path"):
        worker.publish(data, app)
    assert git_calls == []
    assert api_calls == []
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    ("entry", "mismatch"),
    [
        ({"path": "src/deleted.py", "content": None, "mode": "100644"}, "deleted"),
        (
            {"path": "src/fixture.py", "content": b"checked", "mode": "100644"},
            "content",
        ),
    ],
)
def test_publish_rejects_staged_content_mismatch_before_commit_or_push(
    monkeypatch, tmp_path, entry, mismatch
):
    worker, app, data, git_calls, api_calls = _publish_harness(
        tmp_path, monkeypatch, entries=[entry]
    )
    if mismatch == "deleted":
        target = tmp_path / "repo" / entry["path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"original")
    original_git = worker.git

    def mismatched_git(repo, *args):
        if mismatch == "deleted" and args[0] == "ls-files":
            git_calls.append(args)
            return b"still-staged"
        if mismatch == "content" and args[0] == "show":
            git_calls.append(args)
            return b"different"
        return original_git(repo, *args)

    monkeypatch.setattr(worker, "git", mismatched_git)
    expected = (
        "deleted path remains staged"
        if mismatch == "deleted"
        else "staged content differs"
    )
    with pytest.raises(RuntimeContractError, match=expected):
        worker.publish(data, app)
    assert not any("commit" in args for args in git_calls)
    assert not any(args[0] == "push" for args in git_calls)
    assert api_calls == []
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("failure", ["existing-pr", "created-pr"])
def test_publish_rejects_untrusted_pr_attribution_without_receipt(
    monkeypatch, tmp_path, failure
):
    existing = (
        [{"draft": False, "number": 7, "user": {"login": BOT}}]
        if failure == "existing-pr"
        else []
    )
    created = (
        {"draft": False, "user": {"login": "other-bot[bot]"}, "html_url": "synthetic"}
        if failure == "created-pr"
        else None
    )
    worker, app, data, git_calls, api_calls = _publish_harness(
        tmp_path, monkeypatch, existing_pr=existing, write_response=created
    )
    expected = (
        "unexpected existing PR"
        if failure == "existing-pr"
        else "PR attribution mismatch"
    )
    with pytest.raises(RuntimeContractError, match=expected):
        worker.publish(data, app)
    assert sum(args[0] == "push" for args in git_calls) == 1
    writes = [
        method
        for method, _endpoint, _payload in api_calls
        if method in {"POST", "PATCH"}
    ]
    assert writes == ([] if failure == "existing-pr" else ["POST"])
    assert not (tmp_path / "result.json").exists()
