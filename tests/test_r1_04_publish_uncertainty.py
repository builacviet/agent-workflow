"""R1-04A: offline characterization of ambiguous GitHub publish outcomes.

These tests inject an error *after* a simulated remote write was accepted.
They prove local fail-closed behavior, not remote receipt verification, durable
publication intent, real GitHub races, or atomicity across local storage and GitHub.
"""

from __future__ import annotations

import pytest

from agent_workflow.integrations.github import REPOSITORY, WorkflowError
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
            raise WorkflowError("uncertain Git push response")
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
            raise WorkflowError("uncertain PR write response")

    with pytest.raises(WorkflowError, match="uncertain"):
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
